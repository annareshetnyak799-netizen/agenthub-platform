"""Unit tests for router_service routing logic.

The routing functions (build_provider_map, select_provider) are extracted
directly here to avoid importing the full FastAPI module and triggering
prometheus_client global registry conflicts.
"""
from collections import defaultdict
from threading import Lock
from typing import Any

# ---------------------------------------------------------------------------
# Extracted routing functions (mirrors services/router_service/app/main.py)
# ---------------------------------------------------------------------------

round_robin_indices: dict[str, int] = {}
cycle_lock = Lock()


def build_provider_map(providers: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_model: dict = defaultdict(list)
    for provider in providers:
        for model in provider["supported_models"]:
            by_model[model].append(provider)

    expanded: dict[str, list[dict[str, Any]]] = {}
    for model, model_providers in by_model.items():
        weighted = []
        for provider in model_providers:
            weight = max(int(provider.get("weight", 1)), 1)
            weighted.extend([provider] * weight)
        expanded[model] = weighted
    return expanded


def select_provider(
    model: str,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    min_priority = min(int(c.get("priority", 100)) for c in candidates)
    priority_candidates = [c for c in candidates if int(c.get("priority", 100)) == min_priority]

    all_have_latency = all(c.get("last_latency_ms") is not None for c in priority_candidates)

    if all_have_latency:
        min_latency = min(float(c["last_latency_ms"]) for c in priority_candidates)
        best_candidates = [
            c for c in priority_candidates if float(c["last_latency_ms"]) == min_latency
        ]
        strategy = "priority_latency_aware"
    else:
        best_candidates = priority_candidates
        strategy = "model_round_robin"

    if len(best_candidates) == 1:
        return best_candidates[0], strategy

    current_index = round_robin_indices.get(model, 0)
    selected = best_candidates[current_index % len(best_candidates)]
    round_robin_indices[model] = (current_index + 1) % len(best_candidates)
    return selected, strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(provider_id: str, models: list[str], priority: int = 100,
                   weight: int = 1, latency_ms: float | None = None) -> dict:
    return {
        "provider_id": provider_id,
        "provider_name": provider_id,
        "base_url": f"http://{provider_id}:8000",
        "supported_models": models,
        "priority": priority,
        "weight": weight,
        "last_latency_ms": latency_ms,
    }


def _reset_rr() -> None:
    round_robin_indices.clear()


# ---------------------------------------------------------------------------
# build_provider_map
# ---------------------------------------------------------------------------


def test_build_provider_map_groups_by_model():
    providers = [
        _make_provider("a", ["gpt-4"]),
        _make_provider("b", ["gpt-4", "claude"]),
    ]
    pmap = build_provider_map(providers)
    assert "gpt-4" in pmap
    assert "claude" in pmap
    assert len(pmap["gpt-4"]) == 2
    assert len(pmap["claude"]) == 1


def test_build_provider_map_expands_weight():
    providers = [_make_provider("heavy", ["m"], weight=3)]
    pmap = build_provider_map(providers)
    assert len(pmap["m"]) == 3
    assert all(p["provider_id"] == "heavy" for p in pmap["m"])


def test_build_provider_map_weight_at_least_one():
    providers = [_make_provider("zero-weight", ["m"], weight=0)]
    pmap = build_provider_map(providers)
    assert len(pmap["m"]) == 1


# ---------------------------------------------------------------------------
# select_provider — round robin (no latency data)
# ---------------------------------------------------------------------------


def test_select_provider_round_robin_two_cold():
    _reset_rr()
    providers = [_make_provider("a", ["m"]), _make_provider("b", ["m"])]
    pmap = build_provider_map(providers)
    p1, strategy1 = select_provider("m", pmap["m"])
    p2, strategy2 = select_provider("m", pmap["m"])
    assert strategy1 == "model_round_robin"
    assert strategy2 == "model_round_robin"
    assert {p1["provider_id"], p2["provider_id"]} == {"a", "b"}


def test_select_provider_single_candidate():
    _reset_rr()
    providers = [_make_provider("only", ["m"])]
    pmap = build_provider_map(providers)
    p, strategy = select_provider("m", pmap["m"])
    assert p["provider_id"] == "only"
    assert strategy in ("model_round_robin", "priority_latency_aware")


def test_select_provider_round_robin_cycles_fairly():
    _reset_rr()
    providers = [_make_provider("a", ["m"]), _make_provider("b", ["m"])]
    pmap = build_provider_map(providers)
    selected = [select_provider("m", pmap["m"])[0]["provider_id"] for _ in range(4)]
    # Over 4 calls each provider should appear exactly twice.
    assert selected.count("a") == 2
    assert selected.count("b") == 2


# ---------------------------------------------------------------------------
# select_provider — latency-aware (all candidates warm)
# ---------------------------------------------------------------------------


def test_select_provider_latency_aware_picks_fastest():
    _reset_rr()
    providers = [
        _make_provider("fast", ["m"], latency_ms=100.0),
        _make_provider("slow", ["m"], latency_ms=500.0),
    ]
    pmap = build_provider_map(providers)
    p, strategy = select_provider("m", pmap["m"])
    assert p["provider_id"] == "fast"
    assert strategy == "priority_latency_aware"


def test_select_provider_mixed_warm_cold_stays_in_round_robin():
    """Warm-up continues until every priority candidate has latency data."""
    _reset_rr()
    providers = [
        _make_provider("warm", ["m"], latency_ms=200.0),
        _make_provider("cold", ["m"], latency_ms=None),
    ]
    pmap = build_provider_map(providers)
    p1, strategy1 = select_provider("m", pmap["m"])
    p2, strategy2 = select_provider("m", pmap["m"])
    assert strategy1 == "model_round_robin"
    assert strategy2 == "model_round_robin"
    assert {p1["provider_id"], p2["provider_id"]} == {"warm", "cold"}


def test_select_provider_all_cold_falls_back_to_round_robin():
    _reset_rr()
    providers = [
        _make_provider("a", ["m"], latency_ms=None),
        _make_provider("b", ["m"], latency_ms=None),
    ]
    pmap = build_provider_map(providers)
    _, strategy = select_provider("m", pmap["m"])
    assert strategy == "model_round_robin"


# ---------------------------------------------------------------------------
# select_provider — priority filtering
# ---------------------------------------------------------------------------


def test_select_provider_respects_priority():
    _reset_rr()
    providers = [
        _make_provider("low-prio", ["m"], priority=200, latency_ms=50.0),
        _make_provider("high-prio", ["m"], priority=10, latency_ms=300.0),
    ]
    pmap = build_provider_map(providers)
    p, _ = select_provider("m", pmap["m"])
    assert p["provider_id"] == "high-prio"


def test_select_provider_equal_priority_uses_latency():
    _reset_rr()
    providers = [
        _make_provider("a", ["m"], priority=100, latency_ms=100.0),
        _make_provider("b", ["m"], priority=100, latency_ms=400.0),
    ]
    pmap = build_provider_map(providers)
    p, strategy = select_provider("m", pmap["m"])
    assert p["provider_id"] == "a"
    assert strategy == "priority_latency_aware"


def test_select_provider_new_provider_reenters_warmup_round_robin():
    """Adding a cold provider forces a short warm-up round before latency-aware resumes."""
    _reset_rr()
    warm_a = _make_provider("warm-a", ["m"], latency_ms=150.0)
    warm_b = _make_provider("warm-b", ["m"], latency_ms=300.0)
    cold_new = _make_provider("cold-new", ["m"], latency_ms=None)

    pmap = build_provider_map([warm_a, warm_b, cold_new])
    selected = [select_provider("m", pmap["m"])[0]["provider_id"] for _ in range(3)]
    assert set(selected) == {"warm-a", "warm-b", "cold-new"}
