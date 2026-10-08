import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from server_monitor import metrics
from conftest import cpu_times, disk_counters, network_counters


def test_host_measurements_use_actual_elapsed_time_and_consistent_memory(host):
    collector = metrics.MetricsCollector()
    host.now += 2.5
    host.wall += 2.5
    host.cpu = [cpu_times(user=12, idle=92), cpu_times(user=23, idle=81)]
    host.network = {"eth0": network_counters(1_500, 3_000)}
    host.disks = {"sda": disk_counters(11_000, 22_000)}
    host.busy = {"sda": 2_250}

    snapshot = collector.collect()

    assert snapshot["cpu"]["per_core"] == [50.0, 75.0]
    assert snapshot["cpu"]["percent"] == 62.5
    assert snapshot["network"]["sent_bytes_per_sec"] == 200.0
    assert snapshot["network"]["recv_bytes_per_sec"] == 400.0
    assert snapshot["disk_io"]["read_bytes_per_sec"] == 400.0
    assert snapshot["disk_io"]["write_bytes_per_sec"] == 800.0
    assert snapshot["disk_io"]["busy_percent"] == 50.0
    memory = snapshot["memory"]
    assert memory["used_bytes"] + memory["available_bytes"] == memory["total_bytes"]
    assert memory["used_bytes"] / memory["total_bytes"] * 100 == memory["percent"]
    assert memory["cached_bytes"] == 5_000
    assert snapshot["gpus"] == []
    assert snapshot["cpu"]["temperature_celsius"] is None
    assert snapshot["cpu"]["frequency_mhz"] is None
    assert snapshot["system"]["uptime_seconds"] == 102.5
    assert json.loads(json.dumps(snapshot, allow_nan=False)) == snapshot


def test_counter_resets_and_zero_elapsed_never_create_negative_rates(host):
    collector = metrics.MetricsCollector()
    host.network = {"eth0": network_counters(100, 100)}
    host.disks = {"sda": disk_counters(100, 100)}
    host.busy = {"sda": 100}
    host.cpu = [cpu_times(user=1, idle=1), cpu_times(user=1, idle=1)]

    zero_elapsed = collector.collect()
    assert zero_elapsed["network"]["recv_bytes_per_sec"] == 0
    assert zero_elapsed["disk_io"]["read_bytes_per_sec"] == 0
    assert zero_elapsed["cpu"]["percent"] == 0

    host.now += 1
    host.network = {"eth0": network_counters(50, 50)}
    host.disks = {"sda": disk_counters(50, 50)}
    reset = collector.collect()
    assert reset["network"]["sent_bytes_per_sec"] == 0
    assert reset["disk_io"]["write_bytes_per_sec"] == 0
    json.dumps(reset, allow_nan=False)


def test_loopback_is_displayed_but_excluded_from_external_traffic(host):
    host.network["lo"] = network_counters(10, 10)
    collector = metrics.MetricsCollector()
    host.now += 1
    host.network = {
        "eth0": network_counters(1_100, 2_200),
        "lo": network_counters(1_000_010, 1_000_010),
        "new0": network_counters(500_000, 500_000),
    }

    network = collector.collect()["network"]
    interfaces = {entry["name"]: entry for entry in network["interfaces"]}
    assert interfaces["lo"]["recv_bytes_per_sec"] == 1_000_000
    assert interfaces["new0"]["recv_bytes_per_sec"] == 0  # no earlier sample
    assert network["recv_bytes_per_sec"] == 200
    assert network["sent_bytes_per_sec"] == 100


def test_linux_cpu_guest_and_iowait_are_not_double_counted(host):
    host.cpu = [cpu_times(user=10, idle=10, iowait=5, guest=4)]
    collector = metrics.MetricsCollector()
    host.now += 1
    host.cpu = [cpu_times(user=14, idle=14, iowait=7, guest=6)]
    assert collector.collect()["cpu"]["percent"] == 40.0


def test_process_permissions_and_pid_reuse_do_not_misattribute_cpu(host):
    class Process:
        pid = 42
        created = 10
        used = 1

        def oneshot(self):
            return nullcontext()

        def create_time(self):
            return self.created

        def cpu_times(self):
            return SimpleNamespace(user=self.used, system=0)

        def num_threads(self):
            return 3

        def name(self):
            return "worker"

        def memory_percent(self):
            return 2.5

        def memory_info(self):
            return SimpleNamespace(rss=1234)

        def status(self):
            return "running"

    class Restricted(Process):
        pid = 43

        def create_time(self):
            raise metrics.psutil.AccessDenied(self.pid)

    process = Process()
    host.processes = [process, Restricted()]
    collector = metrics.MetricsCollector()
    first = collector.collect()
    assert first["processes"]["total"] == 2
    assert first["processes"]["threads"] == 3
    assert len(first["processes"]["top"]) == 1
    assert "restricted" in " ".join(first["warnings"])

    host.now += 2
    process.used += 1
    active = collector.collect()["processes"]["top"][0]
    assert active["cpu_percent"] == 50
    assert set(active) == {"pid", "name", "cpu_percent", "memory_percent", "memory_bytes", "status"}

    host.now += 1
    process.created = 99  # same PID now belongs to a different process
    process.used = 10
    assert collector.collect()["processes"]["top"][0]["cpu_percent"] == 0


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired("nvidia-smi", 1.5),
        subprocess.CalledProcessError(1, "nvidia-smi"),
        OSError("GPU driver unavailable"),
    ],
)
def test_gpu_driver_errors_leave_other_metrics_available(host, monkeypatch, error):
    monkeypatch.setattr(metrics.shutil, "which", lambda command: "/usr/bin/nvidia-smi")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(metrics.subprocess, "run", fail)
    snapshot = metrics.MetricsCollector().collect()
    assert snapshot["gpus"] == []
    assert "unavailable" in snapshot["gpu_status"]
    assert snapshot["memory"]["total_bytes"] == 16_000


def test_gpu_output_preserves_unsupported_fields_and_converts_mib(host, monkeypatch):
    monkeypatch.setattr(metrics.shutil, "which", lambda command: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        metrics.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout='0, "GPU, quoted name", 25, 512, 2048, N/A, [Not Supported]\n'),
    )
    snapshot = metrics.MetricsCollector().collect()
    gpu = snapshot["gpus"][0]
    assert gpu["name"] == "GPU, quoted name"
    assert gpu["memory_used_bytes"] == 512 * 1024 * 1024
    assert gpu["memory_total_bytes"] == 2048 * 1024 * 1024
    assert gpu["memory_percent"] == 25
    assert gpu["temperature_celsius"] is None
    assert gpu["power_watts"] is None
    assert metrics.history_entry(snapshot)["gpu"] == 25


def test_linux_mounts_filter_virtual_filesystems_and_deduplicate(host, monkeypatch):
    partitions = [
        SimpleNamespace(device="proc", mountpoint="/proc", fstype="proc"),
        SimpleNamespace(device="sysfs", mountpoint="/sys", fstype="sysfs"),
        SimpleNamespace(device="tmpfs", mountpoint="/run", fstype="tmpfs"),
        SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4"),
        SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4"),
        SimpleNamespace(device="/dev/sdb1", mountpoint="/restricted", fstype="ext4"),
    ]
    monkeypatch.setattr(metrics.psutil, "disk_partitions", lambda **kwargs: partitions)
    monkeypatch.setattr(metrics.os.path, "isdir", lambda path: True)
    accessed = []

    def usage(path):
        accessed.append(path)
        if path == "/restricted":
            raise PermissionError("restricted filesystem")
        return SimpleNamespace(total=100, used=40, free=60, percent=40)

    monkeypatch.setattr(metrics.psutil, "disk_usage", usage)
    snapshot = metrics.MetricsCollector().collect()
    assert [disk["mountpoint"] for disk in snapshot["disks"]] == ["/"]
    assert accessed == ["/", "/restricted"]
    assert any("restricted" in warning for warning in snapshot["warnings"])


def test_linux_physical_disk_selection_avoids_partition_and_mapper_double_count(monkeypatch):
    monkeypatch.setattr(metrics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        metrics.Path, "iterdir",
        lambda self: iter(Path(name) for name in ["sda", "nvme0n1", "loop0", "dm-0", "md0"]),
    )
    counters = {name: disk_counters(1, 2) for name in ["sda", "sda1", "nvme0n1", "nvme0n1p1", "loop0", "dm-0", "md0"]}
    assert metrics._physical_disks(counters) == {"sda", "nvme0n1"}


def test_cpu_model_reads_linux_proc(monkeypatch):
    original_read = metrics.Path.read_text

    def read(path, *args, **kwargs):
        if path.as_posix() == "/proc/cpuinfo":
            return "processor : 0\nmodel name : Linux Fixture CPU\n"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(metrics.Path, "read_text", read)
    assert metrics._cpu_model() == "Linux Fixture CPU"


@pytest.mark.parametrize("error", [NotImplementedError(), OSError("missing sensor")])
def test_unsupported_optional_sensors_do_not_break_snapshot(host, monkeypatch, error):
    def fail():
        raise error

    monkeypatch.setattr(metrics.psutil, "sensors_temperatures", fail)
    snapshot = metrics.MetricsCollector().collect()
    assert snapshot["sensors"] == []
    assert snapshot["cpu"]["temperature_celsius"] is None
    assert snapshot["memory"]["total_bytes"] == 16_000


def test_optional_io_failures_at_startup_and_sampling_leave_host_data_available(host, monkeypatch):
    def fail(**kwargs):
        raise OSError("host counters unavailable")

    monkeypatch.setattr(metrics.psutil, "net_io_counters", fail)
    monkeypatch.setattr(metrics.psutil, "disk_io_counters", fail)
    snapshot = metrics.MetricsCollector().collect()
    assert snapshot["memory"]["total_bytes"] == 16_000
    assert snapshot["network"]["interfaces"] == []
    assert snapshot["disk_io"]["busy_percent"] is None
    assert len(snapshot["warnings"]) >= 2
    json.dumps(snapshot, allow_nan=False)


def test_nonfinite_sensor_and_gpu_values_remain_valid_json(host, monkeypatch):
    monkeypatch.setattr(metrics.shutil, "which", lambda command: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        metrics.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="0, GPU, NaN, inf, -inf, NaN, Infinity\n"),
    )
    monkeypatch.setattr(
        metrics.psutil, "sensors_temperatures",
        lambda: {"coretemp": [SimpleNamespace(label="CPU Package", current=float("nan"), high=float("inf"), critical=None)]},
    )
    snapshot = metrics.MetricsCollector().collect()
    assert snapshot["cpu"]["temperature_celsius"] is None
    assert snapshot["gpus"][0]["utilization_percent"] is None
    assert snapshot["gpus"][0]["memory_used_bytes"] is None
    assert metrics.history_entry(snapshot)["gpu"] is None
    json.dumps(snapshot, allow_nan=False)


def test_failed_gpu_query_is_bounded_and_retried_after_backoff(host, monkeypatch):
    monkeypatch.setattr(metrics.shutil, "which", lambda command: "/usr/bin/nvidia-smi")
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired("nvidia-smi", kwargs["timeout"])

    monkeypatch.setattr(metrics.subprocess, "run", fail)
    collector = metrics.MetricsCollector()
    collector.collect()
    host.now += 1
    collector.collect()
    assert len(calls) == 1
    assert calls[0] < 1
    host.now += 5
    collector.collect()
    assert len(calls) == 2


def test_gpu_history_matches_first_device_displayed_in_performance(host):
    snapshot = metrics.MetricsCollector().collect()
    snapshot["gpus"] = [
        {"index": 0, "utilization_percent": None},
        {"index": 1, "utilization_percent": 90.0},
    ]
    assert metrics.history_entry(snapshot)["gpu"] is None
    snapshot["gpus"][0]["utilization_percent"] = 20.0
    assert metrics.history_entry(snapshot)["gpu"] == 20.0


def test_counter_recovery_requires_fresh_baseline_instead_of_spiking(host, monkeypatch):
    collector = metrics.MetricsCollector()

    def fail(**kwargs):
        raise OSError("counter temporarily unavailable")

    monkeypatch.setattr(metrics.psutil, "net_io_counters", fail)
    monkeypatch.setattr(metrics.psutil, "disk_io_counters", fail)
    host.now += 1
    collector.collect()

    monkeypatch.setattr(metrics.psutil, "net_io_counters", lambda **kwargs: host.network)
    monkeypatch.setattr(metrics.psutil, "disk_io_counters", lambda **kwargs: host.disks)
    host.network = {"eth0": network_counters(10_000_000, 20_000_000)}
    host.disks = {"sda": disk_counters(10_000_000, 20_000_000)}
    host.now += 1
    restored = collector.collect()
    assert restored["network"]["recv_bytes_per_sec"] == 0
    assert restored["disk_io"]["read_bytes_per_sec"] == 0

    host.network = {"eth0": network_counters(10_000_100, 20_000_200)}
    host.disks = {"sda": disk_counters(10_000_300, 20_000_400)}
    host.now += 2
    resumed = collector.collect()
    assert resumed["network"]["recv_bytes_per_sec"] == 100
    assert resumed["disk_io"]["read_bytes_per_sec"] == 150
