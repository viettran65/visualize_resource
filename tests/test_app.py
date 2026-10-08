import asyncio
from copy import deepcopy
import importlib
import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest


app_module = importlib.import_module("server_monitor.app")


def sample(timestamp):
    """A valid collector result whose timestamp is controlled by the test."""
    return {
        "timestamp": timestamp,
        "system": {"hostname": "fixture-server"},
        "cpu": {"percent": 37.5},
        "memory": {"percent": 62.5},
        "gpus": [],
        "disk_io": {"busy_percent": None, "read_bytes_per_sec": 100.0, "write_bytes_per_sec": 50.0},
        "network": {"recv_bytes_per_sec": 200.0, "sent_bytes_per_sec": 75.0},
    }


class CountingCollector:
    instances = []

    def __init__(self):
        self.calls = 0
        self.snapshot = sample(time.time())
        self.fail = False
        self.instances.append(self)

    def collect(self):
        self.calls += 1
        if self.fail:
            raise OSError("fixture collector unavailable")
        return deepcopy(self.snapshot)


@pytest.fixture
def collector(monkeypatch):
    CountingCollector.instances = []
    monkeypatch.setattr(app_module, "MetricsCollector", CountingCollector)
    # Requests must reuse the startup sample. This also makes a leaked task
    # visible on context exit without adding a wall-clock sleep to the tests.
    monkeypatch.setattr(app_module, "INTERVAL_SECONDS", 60.0)
    return CountingCollector


def test_api_requests_share_one_sampler_and_shutdown_cancels_it(collector):
    with TestClient(app_module.app) as client:
        monitor = app_module.app.state.monitor
        startup_payload = client.get("/api/snapshot").json()
        responses = [client.get("/api/snapshot") for _ in range(12)]
        assert len(collector.instances) == 1
        assert collector.instances[0].calls == 1
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json() == startup_payload for response in responses)
        assert all(response.headers["cache-control"] == "no-store" for response in responses)
        assert len(startup_payload["history"]) == 1
        assert startup_payload["history"][0]["timestamp"] == startup_payload["snapshot"]["timestamp"]
        assert startup_payload["history"][0]["gpu"] is None
        assert startup_payload["history"][0]["disk"] is None
        assert client.get("/api/health").json()["ready"] is True
        assert monitor.task is not None and not monitor.task.done()
    assert monitor.task.cancelled()


def test_sampler_restarts_cleanly_for_a_new_lifespan(collector):
    with TestClient(app_module.app):
        first = app_module.app.state.monitor
    with TestClient(app_module.app):
        second = app_module.app.state.monitor
        assert first is not second
        assert first.task.cancelled()
        assert second.version == 1
        assert len(second.history) == 1
    assert len(collector.instances) == 2
    assert second.task.cancelled()


def test_lifespan_closes_collector_workers(collector, monkeypatch):
    class ClosingCollector(CountingCollector):
        closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(app_module, "MetricsCollector", ClosingCollector)
    with TestClient(app_module.app):
        monitor = app_module.app.state.monitor
        assert monitor.collector.closed is False
    assert monitor.task.cancelled()
    assert monitor.collector.closed is True


def test_api_failure_and_stale_health_are_reported(collector, monkeypatch):
    class BrokenCollector(CountingCollector):
        def collect(self):
            raise OSError("fixture collector unavailable")

    monkeypatch.setattr(app_module, "MetricsCollector", BrokenCollector)
    with TestClient(app_module.app) as client:
        assert client.get("/api/snapshot").status_code == 503
        health = client.get("/api/health")
        assert health.status_code == 503
        assert health.json()["ready"] is False

    monkeypatch.setattr(app_module, "MetricsCollector", CountingCollector)
    with TestClient(app_module.app) as client:
        monitor = app_module.app.state.monitor
        monitor.last_collection_monotonic = time.monotonic() - 11
        health = client.get("/api/health")
        assert health.status_code == 503
        assert health.json()["sample_age_seconds"] >= 11


def test_history_is_bounded_and_missing_readings_remain_null(collector):
    async def exercise():
        monitor = app_module.MonitorState()
        for index in range(80):
            monitor.collector.snapshot = sample(1_700_000_000.0 + index)
            await monitor.collect()
        assert monitor.version == 80
        assert len(monitor.history) == 60
        assert monitor.history[0]["timestamp"] == 1_700_000_020.0
        assert monitor.payload["history"][-1]["timestamp"] == monitor.payload["snapshot"]["timestamp"]
        assert all(entry["gpu"] is None and entry["disk"] is None for entry in monitor.history)
        json.dumps(monitor.payload, allow_nan=False)

        # A long sampling interruption must evict points outside the chart's
        # time window rather than showing a fictitious continuous history.
        monitor.collector.snapshot = sample(1_700_000_200.0)
        await monitor.collect()
        assert len(monitor.history) == 1

    asyncio.run(exercise())


def test_collection_failure_preserves_last_good_sample_and_recovers(collector):
    async def exercise():
        monitor = app_module.MonitorState()
        await monitor.collect()
        payload = monitor.payload
        monitor.collector.fail = True
        await monitor.collect()
        assert monitor.payload is payload
        assert monitor.version == 1
        assert len(monitor.history) == 1
        assert monitor.last_error == "OSError"
        monitor.collector.fail = False
        monitor.collector.snapshot = sample(time.time() + 1)
        await monitor.collect()
        assert monitor.last_error is None
        assert monitor.version == 2
        assert len(monitor.history) == 2

    asyncio.run(exercise())


def test_sse_emits_cached_sample_then_wakes_on_shared_collection(collector):
    async def exercise():
        monitor = app_module.MonitorState()
        await monitor.collect()
        disconnected = False

        async def is_disconnected():
            return disconnected

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(monitor=monitor)),
            is_disconnected=is_disconnected,
        )
        response = await app_module.events(request)
        assert response.media_type == "text/event-stream"
        assert response.headers["x-accel-buffering"] == "no"
        iterator = response.body_iterator
        first = await asyncio.wait_for(anext(iterator), timeout=1)
        first_lines = first.strip().splitlines()
        assert first_lines[0] == "id: 1"
        assert json.loads(first_lines[1].removeprefix("data: ")) == monitor.payload
        assert monitor.collector.calls == 1

        waiting = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        assert not waiting.done()
        monitor.collector.snapshot = sample(time.time() + 1)
        await monitor.collect()
        second = await asyncio.wait_for(waiting, timeout=1)
        assert second.startswith("id: 2\ndata: ")
        assert monitor.collector.calls == 2
        assert len(json.loads(second.splitlines()[1].removeprefix("data: "))["history"]) == 2
        disconnected = True
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)

    asyncio.run(exercise())
