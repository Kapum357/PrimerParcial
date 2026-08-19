"""Regression tests for UCS state canonicalization and mission solving."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from src.main import _scenario_index, _state_key, solve_agent  # noqa: E402
from src.simulator import initial_state, load_scenario  # noqa: E402


def _custom_two_path_scenario() -> dict:
    return {
        "robot": {"start": "Z1", "battery_max": 50, "battery_start": 50, "cargo_capacity": 10},
        "zones": [
            {"id": "Z1", "name": "START", "recharge": False},
            {"id": "Z2", "name": "MID", "recharge": False},
            {"id": "Z3", "name": "GOAL", "recharge": False},
        ],
        "corridors": [
            {"from": "Z1", "to": "Z2", "cost": 2, "door": None},
            {"from": "Z2", "to": "Z3", "cost": 2, "door": None},
            {"from": "Z1", "to": "Z3", "cost": 5, "door": None},
        ],
        "doors": [],
        "keys": [],
        "tools": [],
        "materials": [],
        "panels": [],
        "stations": [{"id": "GOAL", "kind": "goal", "zone": "Z3", "state": "OFFLINE", "requires": {}}],
        "chargers": [],
        "goal": {"stations_online": ["GOAL"]},
        "action_costs": {"pickup": 1, "drop": 1, "interact": 2, "recharge": 3},
    }


def _custom_impossible_scenario() -> dict:
    scenario = {
        "robot": {"start": "Z1", "battery_max": 50, "battery_start": 10, "cargo_capacity": 2},
        "zones": [
            {"id": "Z1", "name": "START", "recharge": False},
            {"id": "Z2", "name": "LOCKED", "recharge": False},
        ],
        "corridors": [{"from": "Z1", "to": "Z2", "cost": 1, "door": "DOOR_X"}],
        "doors": [{"id": "DOOR_X", "key": "KEY_X", "state": "CLOSED", "between": ["Z1", "Z2"]}],
        "keys": [],
        "tools": [],
        "materials": [],
        "panels": [],
        "stations": [{"id": "GOAL", "kind": "goal", "zone": "Z2", "state": "OFFLINE", "requires": {}}],
        "chargers": [],
        "goal": {"stations_online": ["GOAL"]},
        "action_costs": {"pickup": 1, "drop": 1, "interact": 2, "recharge": 3},
    }
    return scenario


def test_equivalent_states_share_same_logical_key() -> None:
    scenario = load_scenario()
    index = _scenario_index(scenario)
    state_a = initial_state(scenario)
    state_b = copy.deepcopy(state_a)
    state_a["payload"] = [
        {"kind": "tool", "id": "MULTITOOL", "weight": 1},
        {"kind": "material", "type": "FUSE", "weight": 1},
    ]
    state_b["payload"] = [
        {"kind": "material", "type": "FUSE", "weight": 1},
        {"kind": "tool", "id": "MULTITOOL", "weight": 1},
    ]

    assert _state_key(state_a, index) == _state_key(state_b, index)


def test_relevant_information_changes_state_key() -> None:
    scenario = load_scenario()
    index = _scenario_index(scenario)
    state_a = initial_state(scenario)
    state_b = copy.deepcopy(state_a)
    state_b["doors"]["DOOR1"] = "OPEN"

    assert _state_key(state_a, index) != _state_key(state_b, index)


def test_cost_minimization_prefers_cheaper_path_even_with_more_steps() -> None:
    scenario = _custom_two_path_scenario()
    solution = solve_agent(scenario)

    assert solution["solution_found"] is True
    assert solution["status"] == "SUCCESS"
    assert solution["total_cost"] == 6
    assert solution["total_cost"] < 7


def test_impossible_mission_returns_failure() -> None:
    scenario = _custom_impossible_scenario()
    solution = solve_agent(scenario)

    assert solution["solution_found"] is False
    assert solution["status"] == "FAILURE"
    assert "No solution found" in solution["message"] or "Search budget exhausted" in solution["message"]


def test_alternative_routes_reach_same_state_with_minimal_cost() -> None:
    scenario = _custom_two_path_scenario()
    solution = solve_agent(scenario)

    assert solution["solution_found"] is True
    assert solution["total_cost"] == 6
    assert any(step["op"] == "MOVE" for step in solution["steps"])
    assert any(step["to"] == "Z3" for step in solution["steps"] if step["op"] == "MOVE")


if __name__ == "__main__":
    test_equivalent_states_share_same_logical_key()
    test_relevant_information_changes_state_key()
    test_cost_minimization_prefers_cheaper_path_even_with_more_steps()
    test_impossible_mission_returns_failure()
    test_alternative_routes_reach_same_state_with_minimal_cost()
    print("All UCS regression tests passed.")
