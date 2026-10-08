from collections import Counter

from server_monitor import metrics


def stat_line(pid, name, *, state="S", user=20, system=30, threads=4, started=500, pages=3):
    """Linux /proc/PID/stat field numbers, including fields after RSS."""
    values = {
        3: state, 4: 1, 14: user, 15: system,
        20: threads, 22: started, 23: 1_000_000, 24: pages,
    }
    return f"{pid} ({name}) " + " ".join(str(values.get(field, 0)) for field in range(3, 53))


def write_process(root, pid, name, **values):
    directory = root / str(pid)
    directory.mkdir(exist_ok=True)
    (directory / "stat").write_text(stat_line(pid, name, **values))


def enable_proc(monkeypatch, tmp_path):
    monkeypatch.setattr(metrics, "PROC_DIRECTORY", tmp_path)
    monkeypatch.setattr(
        metrics.os, "sysconf",
        lambda key: {"SC_CLK_TCK": 100, "SC_PAGE_SIZE": 4096}[key],
        raising=False,
    )

    def forbidden():
        raise AssertionError("Native procfs sampling must not enumerate psutil processes too")

    monkeypatch.setattr(metrics.psutil, "process_iter", forbidden)


def test_linux_proc_names_cpu_ticks_rss_and_start_time_handle_pid_reuse(host, monkeypatch, tmp_path):
    enable_proc(monkeypatch, tmp_path)
    (tmp_path / "self").mkdir()
    (tmp_path / "net").mkdir()
    name = "worker (pool) ) with spaces"
    write_process(tmp_path, 101, name, state="R")
    collector = metrics.MetricsCollector()

    baseline = collector.collect()["processes"]
    assert baseline["total"] == 1
    assert baseline["threads"] == 4
    assert baseline["top"] == [{
        "pid": 101, "name": name, "cpu_percent": 0.0,
        "memory_percent": 76.8, "memory_bytes": 3 * 4096, "status": "running",
    }]

    host.now += 2
    write_process(tmp_path, 101, name, user=120, system=130, state="S")
    active = collector.collect()["processes"]["top"][0]
    assert active["cpu_percent"] == 100.0  # 200 ticks / 100 Hz / 2 seconds
    assert active["status"] == "sleeping"

    host.now += 1
    write_process(tmp_path, 101, "new process", user=900, system=900, started=950)
    reused = collector.collect()["processes"]["top"][0]
    assert reused["name"] == "new process"
    assert reused["cpu_percent"] == 0.0


def test_linux_proc_permissions_exiting_tasks_and_malformed_stat_are_isolated(host, monkeypatch, tmp_path):
    enable_proc(monkeypatch, tmp_path)
    write_process(tmp_path, 101, "worker", pages=-1)  # Linux can expose a transient negative RSS
    (tmp_path / "102").mkdir()  # exited after PID enumeration, before stat read
    (tmp_path / "103").mkdir()
    (tmp_path / "103" / "stat").write_text("103 (partial) S 1")
    write_process(tmp_path, 104, "restricted")
    original_read = metrics.Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        if path.name == "stat" and path.parent.parent == tmp_path:
            reads.append(path.parent.name)
            if path.parent.name == "104":
                raise PermissionError("proc hidepid restriction")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(metrics.Path, "read_text", read)
    snapshot = metrics.MetricsCollector().collect()
    processes = snapshot["processes"]
    assert processes["total"] == 4
    assert processes["threads"] == 4
    assert [entry["pid"] for entry in processes["top"]] == [101]
    assert processes["top"][0]["memory_bytes"] == 0
    assert Counter(reads) == Counter({"101": 1, "102": 1, "103": 1, "104": 1})
    assert any("1 restricted" in warning for warning in snapshot["warnings"])
    assert any("1 processes" in warning for warning in snapshot["warnings"])


def test_linux_diskstats_busy_field_uses_whole_device_not_partition(monkeypatch):
    diskstats = (
        "8 0 sda 10 0 20 30 40 0 50 60 0 700 800\n"
        "8 1 sda1 10 0 20 30 40 0 50 60 0 650 750\n"
        "259 0 nvme0n1 10 0 20 30 40 0 50 60 0 900 1000\n"
    )
    original_read = metrics.Path.read_text

    def read(path, *args, **kwargs):
        if path.as_posix() == "/proc/diskstats":
            return diskstats
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(metrics.Path, "read_text", read)
    assert metrics._disk_busy_milliseconds({"sda", "nvme0n1"}) == {"sda": 700, "nvme0n1": 900}
