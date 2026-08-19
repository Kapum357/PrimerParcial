"""FastAPI backend — solver for frontend integration testing."""

from __future__ import annotations

import json
import heapq
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .simulator import initial_state

app = FastAPI(title="Emergency Control API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SCENARIO_PATH = Path(__file__).resolve().parents[2] / "scenarios" / "scenario.json"


def _load_default_scenario() -> dict[str, Any]:
    with SCENARIO_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scenario")
def get_scenario() -> dict[str, Any]:
    return _load_default_scenario()


def _spend(state: dict[str, Any], cost: int) -> None:
    state["battery"] -= cost
    state["energy_spent"] += cost


def _weight(state: dict[str, Any]) -> int:
    return sum(int(item.get("weight", 1)) for item in state["payload"])


def _has(state: dict[str, Any], item_id: str | None = None, item_type: str | None = None) -> bool:
    return any(
        (item_id is not None and item.get("id") == item_id)
        or (item_type is not None and item.get("type") == item_type)
        for item in state["payload"]
    )


def _is_dead_item(
    item: dict[str, Any], state: dict[str, Any], index: "_ScenarioIndex"
) -> bool:
    """Return whether an item can no longer enable any future action."""
    if item.get("kind") == "key":
        return any(
            door["key"] == item.get("id") and state["doors"][door_id] == "OPEN"
            for door_id, door in index.doors_by_id.items()
        )
    if item.get("kind") == "tool":
        return not any(
            state["panels"][panel_id] == "DAMAGED"
            and panel["requires"]["tool"] == item.get("id")
            for panel_id, panel in index.panels_by_id.items()
        )
    if item.get("kind") == "material":
        return not any(
            state["panels"][panel_id] == "DAMAGED"
            and panel["requires"]["material"] == item.get("type")
            for panel_id, panel in index.panels_by_id.items()
        )
    return False


def _state_key(
    state: dict[str, Any], index: "_ScenarioIndex"
) -> tuple[Any, ...]:
    payload = tuple(
        sorted((item["kind"], item.get("id") or item.get("type")) for item in state["payload"])
    )
    floor: dict[str, dict[tuple[str, str], int]] = {}
    for item_id, zone in state["ground_keys"].items():
        item = {"kind": "key", "id": item_id}
        if not _is_dead_item(item, state, index):
            floor.setdefault(zone, {})[("key", item_id)] = 1
    for item_id, zone in state["ground_tools"].items():
        item = {"kind": "tool", "id": item_id}
        if not _is_dead_item(item, state, index):
            floor.setdefault(zone, {})[("tool", item_id)] = 1
    for item_type, material in state["ground_materials"].items():
        item = {"kind": "material", "type": item_type}
        if not _is_dead_item(item, state, index):
            floor.setdefault(material["zone"], {})[("material", item_type)] = material["count"]
    floor_key = tuple((zone, tuple(sorted(items.items()))) for zone, items in sorted(floor.items()))
    return (state["zone"], payload, floor_key, tuple(sorted(state["doors"].items())), tuple(sorted(state["panels"].items())), tuple(sorted(state["stations"].items())))


@dataclass(frozen=True)
class _ScenarioIndex:
    """Immutable lookup tables for static scenario data.

    The index avoids repeated linear scans while expanding UCS nodes.  It is
    deliberately separate from the mutable world state: scenario constants
    are shared by every successor and never copied.
    """

    corridors_by_zone: dict[str, tuple[dict[str, Any], ...]]
    keys_by_id: dict[str, dict[str, Any]]
    tools_by_id: dict[str, dict[str, Any]]
    doors_by_id: dict[str, dict[str, Any]]
    panels_by_id: dict[str, dict[str, Any]]
    stations_by_id: dict[str, dict[str, Any]]


def _scenario_index(scenario: dict[str, Any]) -> _ScenarioIndex:
    return _ScenarioIndex(
        corridors_by_zone={
            zone: tuple(c for c in scenario["corridors"] if c["from"] == zone)
            for zone in {c["from"] for c in scenario["corridors"]}
        },
        keys_by_id={item["id"]: item for item in scenario["keys"]},
        tools_by_id={item["id"]: item for item in scenario["tools"]},
        doors_by_id={item["id"]: item for item in scenario["doors"]},
        panels_by_id={item["id"]: item for item in scenario["panels"]},
        stations_by_id={item["id"]: item for item in scenario["stations"]},
    )


def _clone_state(state: dict[str, Any]) -> dict[str, Any]:
    """Copy only the state containers that successor generation may mutate."""
    return {
        **state,
        "payload": list(state["payload"]),
        "doors": dict(state["doors"]),
        "panels": dict(state["panels"]),
        "stations": dict(state["stations"]),
        "ground_keys": dict(state["ground_keys"]),
        "ground_tools": dict(state["ground_tools"]),
        "ground_materials": {
            item_type: dict(material)
            for item_type, material in state["ground_materials"].items()
        },
    }


def _drop_candidates(
    scenario: dict[str, Any], state: dict[str, Any], index: _ScenarioIndex
) -> list[tuple[int, dict[str, Any]]]:
    """Return only payload items whose removal can enable useful progress.

    DROP is not generated as a free relocation action.  It is considered only
    at capacity and only when a useful item is available in the current zone.
    Dead items are preferred; otherwise all payload choices are retained so a
    necessary capacity decision cannot be lost.
    """
    if _weight(state) < scenario["robot"]["cargo_capacity"]:
        return []

    useful_items: set[tuple[str, str]] = set()
    useful_items.update(
        ("key", item_id)
        for item_id, zone in state["ground_keys"].items()
        if zone == state["zone"]
    )
    useful_items.update(
        ("tool", item_id)
        for item_id, zone in state["ground_tools"].items()
        if zone == state["zone"]
    )
    useful_items.update(
        ("material", item_type)
        for item_type, material in state["ground_materials"].items()
        if material["zone"] == state["zone"] and material["count"] > 0
    )
    if not useful_items:
        return []

    dead: list[tuple[int, dict[str, Any]]] = []
    for item_index, item in enumerate(state["payload"]):
        if item.get("kind") == "key":
            door_is_open = any(
                door["key"] == item.get("id")
                and state["doors"][door_id] == "OPEN"
                for door_id, door in index.doors_by_id.items()
            )
            if door_is_open:
                dead.append((item_index, item))
        elif item.get("kind") == "tool":
            still_needed = any(
                state["panels"][panel_id] == "DAMAGED"
                and panel["requires"]["tool"] == item.get("id")
                for panel_id, panel in index.panels_by_id.items()
            )
            if not still_needed:
                dead.append((item_index, item))
        elif item.get("kind") == "material":
            still_needed = any(
                state["panels"][panel_id] == "DAMAGED"
                and panel["requires"]["material"] == item.get("type")
                for panel_id, panel in index.panels_by_id.items()
            )
            if not still_needed:
                dead.append((item_index, item))
    return dead


def _action_costs(scenario: dict[str, Any]) -> dict[str, int]:
    configured = scenario.get("action_costs", {})
    return {name: int(configured.get(name, default)) for name, default in (("pickup", 1), ("drop", 1), ("interact", 2), ("recharge", 3))}


def _failure_result(message: str = "FAILURE") -> dict[str, Any]:
    return {
        "solution_found": False,
        "status": "FAILURE",
        "total_cost": 0,
        "steps": [],
        "message": message,
    }


def _success_result(path_cost: int, path: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "solution_found": True,
        "status": "SUCCESS",
        "total_cost": path_cost,
        "steps": path,
        "message": f"UCS solution found with cost {path_cost}",
    }


def _move_successors(
    state: dict[str, Any], scenario_index: _ScenarioIndex
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    zone = state["zone"]
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for corridor in scenario_index.corridors_by_zone.get(zone, ()):
        door = corridor.get("door")
        cost = int(corridor["cost"])
        if (door and state["doors"][door] != "OPEN") or state["battery"] < cost:
            continue
        next_state = _clone_state(state)
        _spend(next_state, cost)
        next_state["zone"] = corridor["to"]
        result.append((next_state, {"op": "MOVE", "from": zone, "to": corridor["to"], "cost": cost}))
    return result


def _pickup_successors(
    scenario: dict[str, Any],
    state: dict[str, Any],
    scenario_index: _ScenarioIndex,
    costs: dict[str, int],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    zone = state["zone"]
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if state["battery"] < costs["pickup"] or _weight(state) >= scenario["robot"]["cargo_capacity"]:
        return result

    for item_id, item_zone in state["ground_keys"].items():
        if item_zone != zone or _is_dead_item({"kind": "key", "id": item_id}, state, scenario_index):
            continue
        definition = scenario_index.keys_by_id[item_id]
        next_state = _clone_state(state)
        _spend(next_state, costs["pickup"])
        del next_state["ground_keys"][item_id]
        next_state["payload"].append({"kind": "key", **definition})
        result.append((next_state, {"op": "PICKUP", "item": item_id, "cost": costs["pickup"]}))

    for item_id, item_zone in state["ground_tools"].items():
        if item_zone != zone or _is_dead_item({"kind": "tool", "id": item_id}, state, scenario_index):
            continue
        definition = scenario_index.tools_by_id[item_id]
        next_state = _clone_state(state)
        _spend(next_state, costs["pickup"])
        del next_state["ground_tools"][item_id]
        next_state["payload"].append({"kind": "tool", **definition})
        result.append((next_state, {"op": "PICKUP", "item": item_id, "cost": costs["pickup"]}))

    for item_type, material in state["ground_materials"].items():
        if (
            material["zone"] != zone
            or material["count"] <= 0
            or _is_dead_item({"kind": "material", "type": item_type}, state, scenario_index)
        ):
            continue
        next_state = _clone_state(state)
        _spend(next_state, costs["pickup"])
        next_state["ground_materials"][item_type]["count"] -= 1
        if next_state["ground_materials"][item_type]["count"] == 0:
            del next_state["ground_materials"][item_type]
        next_state["payload"].append({"kind": "material", "type": item_type, "weight": 1})
        result.append((next_state, {"op": "PICKUP", "item": item_type, "cost": costs["pickup"]}))
    return result


def _door_successors(
    state: dict[str, Any],
    scenario_index: _ScenarioIndex,
    costs: dict[str, int],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    zone = state["zone"]
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for door_id, door in scenario_index.doors_by_id.items():
        if state["doors"][door_id] == "OPEN" or zone not in door["between"] or not _has(state, item_id=door["key"]):
            continue
        if state["battery"] < costs["interact"]:
            continue
        next_state = _clone_state(state)
        _spend(next_state, costs["interact"])
        next_state["doors"][door_id] = "OPEN"
        result.append((next_state, {"op": "INTERACT", "target": door_id, "action": "OPEN_DOOR", "cost": costs["interact"]}))
    return result


def _panel_successors(
    state: dict[str, Any],
    scenario_index: _ScenarioIndex,
    costs: dict[str, int],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    zone = state["zone"]
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for panel in scenario_index.panels_by_id.values():
        required = panel["requires"]
        if (
            state["panels"][panel["id"]] != "DAMAGED"
            or zone != panel["zone"]
            or not _has(state, item_id=required["tool"])
            or not _has(state, item_type=required["material"])
        ):
            continue
        if state["battery"] < costs["interact"]:
            continue
        next_state = _clone_state(state)
        _spend(next_state, costs["interact"])
        index = next(i for i, item in enumerate(next_state["payload"]) if item.get("type") == required["material"])
        next_state["payload"].pop(index)
        next_state["panels"][panel["id"]] = "OK"
        result.append((next_state, {"op": "INTERACT", "target": panel["id"], "action": "REPAIR", "consumes": required["material"], "cost": costs["interact"]}))
    return result


def _station_successors(
    state: dict[str, Any],
    scenario_index: _ScenarioIndex,
    costs: dict[str, int],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    zone = state["zone"]
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for station_id, station in scenario_index.stations_by_id.items():
        requirements = station.get("requires", {})
        ready = all(state["panels"][pid] == "OK" for pid in requirements.get("panels_ok", [])) and all(state["stations"][sid] == "ONLINE" for sid in requirements.get("stations_online", []))
        if state["stations"][station["id"]] != "OFFLINE" or zone != station["zone"] or not ready or state["battery"] < costs["interact"]:
            continue
        next_state = _clone_state(state)
        _spend(next_state, costs["interact"])
        next_state["stations"][station_id] = "ONLINE"
        result.append((next_state, {"op": "INTERACT", "target": station_id, "action": "ACTIVATE", "cost": costs["interact"]}))
    return result


def _recharge_successors(
    scenario: dict[str, Any],
    state: dict[str, Any],
    costs: dict[str, int],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    zone = state["zone"]
    maximum = scenario["robot"]["battery_max"]
    if state["battery"] >= maximum or state["battery"] < costs["recharge"]:
        return []
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for charger in scenario.get("chargers", []):
        if charger["zone"] != zone:
            continue
        next_state = _clone_state(state)
        _spend(next_state, costs["recharge"])
        next_state["battery"] = maximum
        result.append((next_state, {"op": "INTERACT", "target": charger["id"], "action": "RECHARGE", "cost": costs["recharge"]}))
    return result


def _drop_successors(
    scenario: dict[str, Any],
    state: dict[str, Any],
    scenario_index: _ScenarioIndex,
    costs: dict[str, int],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if state["battery"] < costs["drop"]:
        return []
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item_index, item in _drop_candidates(scenario, state, scenario_index):
        next_state = _clone_state(state)
        _spend(next_state, costs["drop"])
        dropped = next_state["payload"].pop(item_index)
        if dropped["kind"] == "key":
            next_state["ground_keys"][dropped["id"]] = state["zone"]
        elif dropped["kind"] == "tool":
            next_state["ground_tools"][dropped["id"]] = state["zone"]
        else:
            material = next_state["ground_materials"].setdefault(
                dropped["type"], {"type": dropped["type"], "count": 0, "zone": state["zone"]}
            )
            material["count"] += 1
        result.append((next_state, {"op": "DROP", "item": dropped.get("id") or dropped.get("type"), "cost": costs["drop"]}))
    return result


def _successors(
    scenario: dict[str, Any], state: dict[str, Any], scenario_index: _ScenarioIndex
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    costs = _action_costs(scenario)
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    result.extend(_move_successors(state, scenario_index))
    result.extend(_pickup_successors(scenario, state, scenario_index, costs))
    result.extend(_door_successors(state, scenario_index, costs))
    result.extend(_panel_successors(state, scenario_index, costs))
    result.extend(_station_successors(state, scenario_index, costs))
    result.extend(_recharge_successors(scenario, state, costs))
    result.extend(_drop_successors(scenario, state, scenario_index, costs))
    return result


def solve_agent(scenario: dict[str, Any]) -> dict[str, Any]:
    start = initial_state(scenario)
    scenario_index = _scenario_index(scenario)
    start_key = _state_key(start, scenario_index)
    frontier = [(0, 0, start_key, start, [])]
    labels: dict[tuple[Any, ...], list[tuple[int, int]]] = {
        start_key: [(0, start["battery"])]
    }
    counter = 1
    expansions = 0
    expansion_limit = 50000
    while frontier:
        expansions += 1
        if expansions > expansion_limit:
            return _failure_result("Search budget exhausted without finding a solution")
        path_cost, _, key, state, path = heapq.heappop(frontier)
        if (path_cost, state["battery"]) not in labels.get(key, []):
            continue
        if all(state["stations"][sid] == "ONLINE" for sid in scenario["goal"]["stations_online"]):
            return _success_result(path_cost, path)
        for next_state, action in _successors(scenario, state, scenario_index):
            next_cost = path_cost + int(action["cost"])
            next_key = _state_key(next_state, scenario_index)
            next_label = (next_cost, next_state["battery"])
            previous_labels = labels.setdefault(next_key, [])
            if any(cost <= next_cost and battery >= next_state["battery"] for cost, battery in previous_labels):
                continue
            labels[next_key] = [
                (cost, battery)
                for cost, battery in previous_labels
                if not (next_cost <= cost and next_state["battery"] >= battery)
            ]
            labels[next_key].append(next_label)
            heapq.heappush(frontier, (next_cost, counter, next_key, next_state, path + [action]))
            counter += 1
    return _failure_result("No solution found")


@app.post("/api/solve")
def solve(scenario: dict[str, Any]) -> dict[str, Any]:
    """Return a minimum-cost plan consistent with the provided scenario."""
    data = scenario if scenario else _load_default_scenario()
    return solve_agent(data)
