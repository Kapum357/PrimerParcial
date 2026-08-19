"""FastAPI backend — solver for frontend integration testing."""

from __future__ import annotations

import json
import heapq
from collections import deque
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

SCENARIO_PATH = Path(__file__).resolve().parent.parent.parent / "scenarios" / "scenario.json"


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


# --- Clasificación y detección atómica de items muertos ---

def _is_dead_key(
    item: dict[str, Any], state: dict[str, Any], index: _ScenarioIndex
) -> bool:
    """Una llave está muerta si no queda ninguna puerta cerrada que la requiera."""
    key_id = item.get("id")
    return not any(
        door.get("key") == key_id and state["doors"].get(door_id) != "OPEN"
        for door_id, door in index.doors_by_id.items()
    )


def _is_dead_tool(
    item: dict[str, Any], state: dict[str, Any], index: _ScenarioIndex
) -> bool:
    """Una herramienta está muerta si ningún panel dañado la requiere."""
    tool_id = item.get("id")
    return not any(
        state["panels"].get(panel_id) == "DAMAGED"
        and panel.get("requires", {}).get("tool") == tool_id
        for panel_id, panel in index.panels_by_id.items()
    )


def _is_dead_material(
    item: dict[str, Any], state: dict[str, Any], index: _ScenarioIndex
) -> bool:
    """Un material está muerto si ningún panel dañado requiere este tipo."""
    mat_type = item.get("type")
    return not any(
        state["panels"].get(panel_id) == "DAMAGED"
        and panel.get("requires", {}).get("material") == mat_type
        for panel_id, panel in index.panels_by_id.items()
    )


def _is_dead_item(
    item: dict[str, Any], state: dict[str, Any], index: _ScenarioIndex
) -> bool:
    """Wrapper para compatibilidad con _state_key y filtros de suelo."""
    kind = item.get("kind")
    if kind == "key":
        return _is_dead_key(item, state, index)
    if kind == "tool":
        return _is_dead_tool(item, state, index)
    if kind == "material":
        return _is_dead_material(item, state, index)
    return False


def _classify_payload_items(
    state: dict[str, Any], index: _ScenarioIndex
) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """Clasifica los items del payload en dead_keys, dead_tools, dead_materials y alive."""
    dead_keys: list[tuple[int, dict[str, Any]]] = []
    dead_tools: list[tuple[int, dict[str, Any]]] = []
    dead_materials: list[tuple[int, dict[str, Any]]] = []
    alive: list[tuple[int, dict[str, Any]]] = []

    for idx, item in enumerate(state.get("payload", [])):
        kind = item.get("kind")
        if kind == "key":
            (dead_keys if _is_dead_key(item, state, index) else alive).append((idx, item))
        elif kind == "tool":
            (dead_tools if _is_dead_tool(item, state, index) else alive).append((idx, item))
        elif kind == "material":
            (dead_materials if _is_dead_material(item, state, index) else alive).append((idx, item))
        else:
            alive.append((idx, item))

    return {
        "dead_keys": dead_keys,
        "dead_tools": dead_tools,
        "dead_materials": dead_materials,
        "alive": alive,
    }


# --- Helpers de utilidad y distancias de zonas ---

def _useful_items_in_zone(state: dict[str, Any], zone: str) -> set[tuple[str, str]]:
    """Items en el suelo de 'zone' que aún tienen utilidad y pueden recogerse."""
    useful: set[tuple[str, str]] = set()
    useful.update(
        ("key", item_id)
        for item_id, item_zone in state.get("ground_keys", {}).items()
        if item_zone == zone
    )
    useful.update(
        ("tool", item_id)
        for item_id, item_zone in state.get("ground_tools", {}).items()
        if item_zone == zone
    )
    useful.update(
        ("material", item_type)
        for item_type, material in state.get("ground_materials", {}).items()
        if material.get("zone") == zone and material.get("count", 0) > 0
    )
    return useful


def _get_zone_distance(z1: str, z2: str, index: _ScenarioIndex) -> int:
    """Distancia mínima en saltos entre zonas vía BFS."""
    if z1 == z2:
        return 0
    visited = {z1}
    queue: deque[tuple[str, int]] = deque([(z1, 0)])
    while queue:
        curr, dist = queue.popleft()
        if curr == z2:
            return dist
        for corridor in index.corridors_by_zone.get(curr, ()):
            nxt = corridor.get("to")
            if nxt and nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, dist + 1))
    return 999


def _sort_by_utility(
    alive_items: list[tuple[int, dict[str, Any]]],
    state: dict[str, Any],
    index: _ScenarioIndex,
    current_zone: str,
) -> list[tuple[int, dict[str, Any]]]:
    """Ordena items vivos de menor a mayor urgencia para guiar la poda."""
    def utility_score(entry: tuple[int, dict[str, Any]]) -> float:
        _, item = entry
        kind = item.get("kind")
        if kind == "key":
            key_id = item.get("id")
            closed_doors = [
                d for d in index.doors_by_id.values()
                if d.get("key") == key_id and state["doors"].get(d["id"]) != "OPEN"
            ]
            if not closed_doors:
                return 0.0
            return float(min(
                min(_get_zone_distance(current_zone, z, index) for z in d.get("between", [current_zone]))
                for d in closed_doors
            ))
        if kind == "tool":
            tool_id = item.get("id")
            needed_panels = [
                p for p in index.panels_by_id.values()
                if p.get("requires", {}).get("tool") == tool_id and state["panels"].get(p["id"]) == "DAMAGED"
            ]
            return float(len(needed_panels) * 10)
        if kind == "material":
            mat_type = item.get("type")
            needed_panels = [
                p for p in index.panels_by_id.values()
                if p.get("requires", {}).get("material") == mat_type and state["panels"].get(p["id"]) == "DAMAGED"
            ]
            return float(len(needed_panels) * 10)
        return 100.0

    return sorted(alive_items, key=utility_score)


def _drop_candidates(
    scenario: dict[str, Any], state: dict[str, Any], index: _ScenarioIndex
) -> list[tuple[int, dict[str, Any]]]:
    """Genera candidatos a soltar: muertos preferidos, vivos ordenados por utilidad solo a capacidad."""
    # 1. Filtro de capacidad
    if _weight(state) < scenario.get("robot", {}).get("cargo_capacity", 0):
        return []

    # 2. Filtro de zona útil (evita explosión en zonas vacías)
    useful_here = _useful_items_in_zone(state, state["zone"])
    if not useful_here:
        return []

    classified = _classify_payload_items(state, index)
    all_dead = classified["dead_keys"] + classified["dead_tools"] + classified["dead_materials"]

    # 3. Preferencia: si hay muertos, soltar muertos
    if all_dead:
        return all_dead

    # 4. Si no hay muertos y estamos a capacidad, soltar el vivo menos urgente
    return _sort_by_utility(classified["alive"], state, index, state["zone"])


def _state_key(
    state: dict[str, Any], index: _ScenarioIndex
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
    return (
        state["zone"],
        payload,
        floor_key,
        tuple(sorted(state["doors"].items())),
        tuple(sorted(state["panels"].items())),
        tuple(sorted(state["stations"].items())),
    )


@dataclass(frozen=True)
class _ScenarioIndex:
    """Lookup tables inmutables para datos estáticos del escenario."""

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
    """Copia únicamente las estructuras de estado que las transiciones pueden mutar."""
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
        if (door and state["doors"].get(door) != "OPEN") or state["battery"] < cost:
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
        if state["doors"].get(door_id) == "OPEN" or zone not in door["between"] or not _has(state, item_id=door["key"]):
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
            state["panels"].get(panel["id"]) != "DAMAGED"
            or zone != panel["zone"]
            or not _has(state, item_id=required["tool"])
            or not _has(state, item_type=required["material"])
        ):
            continue
        if state["battery"] < costs["interact"]:
            continue
        next_state = _clone_state(state)
        _spend(next_state, costs["interact"])
        idx = next(i for i, item in enumerate(next_state["payload"]) if item.get("type") == required["material"])
        next_state["payload"].pop(idx)
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
        ready = (
            all(state["panels"].get(pid) == "OK" for pid in requirements.get("panels_ok", []))
            and all(state["stations"].get(sid) == "ONLINE" for sid in requirements.get("stations_online", []))
        )
        if state["stations"].get(station["id"]) != "OFFLINE" or zone != station["zone"] or not ready or state["battery"] < costs["interact"]:
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
    configured_costs = scenario.get("action_costs", {})
    costs = {
        "pickup": int(configured_costs.get("pickup", 1)),
        "drop": int(configured_costs.get("drop", 1)),
        "interact": int(configured_costs.get("interact", 2)),
        "recharge": int(configured_costs.get("recharge", 3)),
    }
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
    while frontier:
        path_cost, _, key, state, path = heapq.heappop(frontier)
        if (path_cost, state["battery"]) not in labels.get(key, []):
            continue
        if all(state["stations"].get(sid) == "ONLINE" for sid in scenario["goal"]["stations_online"]):
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