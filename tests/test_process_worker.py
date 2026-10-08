from threading import Event, Lock
import time

from server_monitor import metrics


def test_slow_portable_process_queries_use_one_worker_and_preserve_sample_freshness(host, monkeypatch):
    monkeypatch.setattr(metrics.platform, "system", lambda: "Windows")
    monkeypatch.setattr(metrics.psutil, "pids", lambda: [101, 102])
    started = Event()
    release = Event()
    lock = Lock()
    calls = active = max_active = 0

    def slow_processes(self, elapsed, warnings, memory_total=None):
        nonlocal calls, active, max_active
        with lock:
            calls += 1
            active += 1
            max_active = max(max_active, active)
        started.set()
        try:
            if not release.wait(timeout=3):
                raise TimeoutError("test process reader was not released")
            return {"total": 2, "threads": 8, "top": []}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(metrics.MetricsCollector, "_processes", slow_processes)
    collector = metrics.MetricsCollector()
    try:
        initial_time = host.wall
        before = time.perf_counter()
        first = collector.collect()
        assert time.perf_counter() - before < 0.5
        assert started.wait(timeout=1)
        assert first["processes"] == {"total": 2, "threads": None, "top": [], "sampled_at": None}
        future = collector._process_future
        for _ in range(5):
            collector.collect()
        assert collector._process_future is future
        assert calls == 1  # repeated requests/samples never queue additional work
        assert max_active == 1

        release.set()
        future.result(timeout=1)
        host.now += 2
        host.wall += 2
        cached = collector.collect()
        assert cached["processes"]["threads"] == 8
        assert cached["processes"]["sampled_at"] == initial_time
        assert cached["processes"]["sampled_at"] < cached["timestamp"]
        assert max_active == 1
    finally:
        release.set()
        collector.close()
    assert all(not thread.is_alive() for thread in collector._process_executor._threads)
    collector.close()  # cleanup is safe when called more than once
