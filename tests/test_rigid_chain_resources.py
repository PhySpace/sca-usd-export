"""Large-chain resource guards and unchanged material collision semantics."""
from itertools import product
from copy import deepcopy

import numpy as np
import pytest

from sca_usd_export.rigid_joint_chain import material_filter_indices, preflight_resources


def budget(**overrides):
    return {"max_bodies": 256, "max_joints": 256, "max_colliders": 256,
            "max_filtered_pairs": 4096, "max_contact_pairs": 32768,
            "max_solver_work_per_second": 80_000_000, **overrides}


def resource_declaration(count, **overrides):
    return {"geometry": {"free_start": 0, "free_end": .1, "length": .1,
                         "centerline": [[0, 0, 0], [.1, 0, 0]], "arc_stations": [0, .1]},
            "discretization": {"free_segments": count},
            "section": {"radius": .001, "endpoint_radius": .001},
            "mechanics": {"linear_density": .04},
            "collision": {"self_collision": True, "filter_adjacent": True, "filter_endpoint_links": True},
            "body": {"solver_position_iterations": 64, "solver_velocity_iterations": 8},
            "scene": {"physics_hz": 480},
            "endpoints": {"start": {"mode": "world_fixed"}, "end": {"mode": "free"}},
            "resource_budget": budget(), **overrides}


def empty_report():
    return {"body_count": 0, "joint_count": 0, "collider_count": 0,
            "groups": [], "collision_exclusions": []}


def test_material_filter_window_matches_independent_all_pair_reference():
    random = np.random.default_rng(20261022)
    for count in (2, 7, 83, 513):
        spans = random.uniform(.0001, .01, count)
        radii = random.uniform(.0002, .003, count)
        # An unusually large endpoint must not prematurely end the search.
        radii[-1] = .025
        stations = np.r_[0.0, np.cumsum(spans)]
        plan = {"arc_lengths_m": spans, "stations_m": stations, "radii_m": radii}
        for self_collision, adjacent, neighbors in product((False, True), repeat=3):
            collision = {"self_collision": self_collision, "filter_adjacent": adjacent,
                         "filter_material_neighbors": neighbors}
            expected = {(i, j) for i in range(count) for j in range(i + 1, count)
                        if (not self_collision or (adjacent and j == i + 1)
                            or (neighbors and stations[j] - stations[i + 1]
                                <= radii[i] + radii[j] + 1e-12))}
            actual = list(material_filter_indices(plan, {"collision": collision}))
            assert len(actual) == len(set(actual))
            assert set(actual) == expected


def test_large_self_colliding_chain_only_emits_adjacent_filters():
    count = 100_000
    plan = {"arc_lengths_m": np.full(count, .01),
            "stations_m": np.arange(count + 1) * .01,
            "radii_m": np.full(count, .001)}
    declaration = {"collision": {"self_collision": True, "filter_adjacent": True}}
    pairs = list(material_filter_indices(plan, declaration))
    assert len(pairs) == count - 1
    assert pairs[0] == (0, 1) and pairs[-1] == (count - 2, count - 1)


def test_huge_request_rejected_before_any_discretization_allocation(monkeypatch):
    def forbidden(*args):
        pytest.fail("budget must reject before plan_chain allocates")
    monkeypatch.setattr("sca_usd_export.rigid_joint_chain.plan_chain", forbidden)
    with pytest.raises(ValueError, match="bodies=1000000000 > 256"):
        preflight_resources({"huge": resource_declaration(1_000_000_000)}, empty_report())


def test_disabled_self_collision_pair_budget_checked_without_pair_enumeration(monkeypatch):
    item = resource_declaration(513, resource_budget=budget(max_bodies=1024, max_joints=1024,
                                                          max_colliders=1024, max_contact_pairs=1_000_000))
    item["collision"]["self_collision"] = False
    def forbidden(*args):
        pytest.fail("all-pair count must be computed without enumerating pairs")
    monkeypatch.setattr("sca_usd_export.rigid_joint_chain.material_filter_indices", forbidden)
    with pytest.raises(ValueError, match="filtered_pairs=131328 > 4096"):
        preflight_resources({"many": item}, empty_report())


def test_scene_budget_includes_other_chains_and_ordinary_colliders():
    one = resource_declaration(12)
    two = resource_declaration(9)
    del two["resource_budget"]  # A legacy chain still consumes scene resources.
    report = empty_report()
    report.update(body_count=2, collider_count=3, joint_count=1)
    report["groups"] = [{"members": ["handle"], "parts": [{}, {}]}]
    one["endpoints"]["end"] = {"mode": "body_fixed", "rigid_node_id": "handle"}
    resources = preflight_resources({"one": one, "two": two}, report)
    assert resources["estimated_usage"] == {"bodies": 23, "joints": 23, "colliders": 24,
                                             "filtered_pairs": 21, "contact_pairs": 255,
                                             "solver_work_per_second": 725760}
    assert resources["chain_counts"]["one"]["filtered_pairs"] == 13
    two["resource_budget"] = budget(max_bodies=22)
    with pytest.raises(ValueError, match="bodies=23 > 22"):
        preflight_resources({"one": one, "two": two}, report)


@pytest.mark.parametrize("field,limit", [("max_contact_pairs", 1), ("max_solver_work_per_second", 1),
                                         ("max_colliders", 1), ("max_joints", 1), ("max_filtered_pairs", 1)])
def test_each_resource_dimension_is_enforced(field, limit):
    item = resource_declaration(7, resource_budget=budget(**{field: limit}))
    with pytest.raises(ValueError, match="resource budget exceeded"):
        preflight_resources({"one": item}, empty_report())


def test_legacy_declarations_without_budget_do_not_gain_new_rejections():
    one = resource_declaration(512)
    del one["resource_budget"]
    assert preflight_resources({"legacy": one}, empty_report()) is None
