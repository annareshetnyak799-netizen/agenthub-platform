"""Unit tests for provider_registry reconciliation and snapshot logic.

Pure Python — no Docker, no HTTP, no FastAPI import.
"""
import json
import os
import sys
import time

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Minimal ProviderRecord replica (mirrors provider_registry ProviderRecord)
# ---------------------------------------------------------------------------


class ProviderRecord(BaseModel):
    provider_id: str
    provider_name: str
    base_url: str
    supported_models: list[str]
    weight: int = Field(default=1, ge=1)
    price_input_per_1k: float = Field(default=0.0, ge=0.0)
    price_output_per_1k: float = Field(default=0.0, ge=0.0)
    rate_limit: int | None = Field(default=None, ge=1)
    priority: int = 100
    enabled: bool = True
    health_status: str = "healthy"
    last_latency_ms: float | None = Field(default=None, ge=0.0)
    failure_count: int = Field(default=0, ge=0)
    cooldown_until: float | None = None


def reconcile_provider(provider: ProviderRecord) -> ProviderRecord:
    if (
        provider.health_status == "unhealthy"
        and provider.cooldown_until is not None
        and provider.cooldown_until <= time.time()
    ):
        return provider.model_copy(
            update={"health_status": "healthy", "cooldown_until": None, "failure_count": 0}
        )
    return provider


def _make(**overrides) -> ProviderRecord:
    base = dict(
        provider_id="p1",
        provider_name="p1",
        base_url="http://p1:8000",
        supported_models=["m"],
        health_status="healthy",
        failure_count=0,
        cooldown_until=None,
    )
    base.update(overrides)
    return ProviderRecord(**base)


# ---------------------------------------------------------------------------
# reconcile_provider
# ---------------------------------------------------------------------------


def test_reconcile_healthy_provider_unchanged():
    p = _make()
    result = reconcile_provider(p)
    assert result is p


def test_reconcile_unhealthy_after_cooldown_resets():
    p = _make(
        health_status="unhealthy",
        failure_count=3,
        cooldown_until=time.time() - 1,
    )
    result = reconcile_provider(p)
    assert result.health_status == "healthy"
    assert result.failure_count == 0
    assert result.cooldown_until is None


def test_reconcile_unhealthy_during_cooldown_unchanged():
    p = _make(
        health_status="unhealthy",
        failure_count=2,
        cooldown_until=time.time() + 60,
    )
    result = reconcile_provider(p)
    assert result.health_status == "unhealthy"
    assert result.failure_count == 2


def test_reconcile_unhealthy_no_cooldown_until_unchanged():
    p = _make(health_status="unhealthy", cooldown_until=None)
    result = reconcile_provider(p)
    assert result.health_status == "unhealthy"


def test_reconcile_idempotent():
    """Calling reconcile twice on a recovered provider is a no-op."""
    p = _make(
        health_status="unhealthy",
        failure_count=1,
        cooldown_until=time.time() - 1,
    )
    first = reconcile_provider(p)
    second = reconcile_provider(first)
    assert first is second  # already healthy, no copy on second call


# ---------------------------------------------------------------------------
# Snapshot helpers (imported directly to avoid prometheus_client conflicts)
# ---------------------------------------------------------------------------


def test_snapshot_roundtrip(tmp_path):
    """save_snapshot → load_snapshot returns equivalent records."""
    # Import snapshot module directly using its file path.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "provider_snapshot",
        os.path.join(
            os.path.dirname(__file__),
            "../../services/provider_registry/app/snapshot.py",
        ),
    )
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    snap.DATA_DIR = str(tmp_path)
    snap._SNAPSHOT_FILE = str(tmp_path / "providers.json")

    assert snap.load_snapshot() is None  # File does not exist yet.

    records = [{"provider_id": "x", "provider_name": "x", "base_url": "http://x",
                "supported_models": ["m"], "weight": 1}]
    snap.save_snapshot(records)
    loaded = snap.load_snapshot()

    assert loaded is not None
    assert loaded[0]["provider_id"] == "x"


def test_snapshot_atomic_write(tmp_path):
    """After save_snapshot the file contains valid JSON (atomic replace)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "provider_snapshot2",
        os.path.join(
            os.path.dirname(__file__),
            "../../services/provider_registry/app/snapshot.py",
        ),
    )
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    snap.DATA_DIR = str(tmp_path)
    snap._SNAPSHOT_FILE = str(tmp_path / "providers.json")

    records = [{"id": i} for i in range(10)]
    snap.save_snapshot(records)

    with open(snap._SNAPSHOT_FILE) as fh:
        data = json.load(fh)
    assert len(data) == 10
