"""Deterministic host measurements, without requiring a GPU or elevated access."""

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from server_monitor import metrics


CpuTimes = namedtuple(
    "CpuTimes", "user nice system idle iowait irq softirq steal guest guest_nice"
)


def cpu_times(*, user=0, idle=0, iowait=0, guest=0):
    return CpuTimes(user, 0, 0, idle, iowait, 0, 0, 0, guest, 0)


def network_counters(sent=0, recv=0):
    return SimpleNamespace(bytes_sent=sent, bytes_recv=recv)


def disk_counters(read=0, write=0):
    return SimpleNamespace(read_bytes=read, write_bytes=write)


@pytest.fixture
def host(monkeypatch):
    """A mutable host frame; moving now changes the actual sample interval."""
    frame = SimpleNamespace(
        now=100.0,
        wall=1_700_000_100.0,
        cpu=[cpu_times(user=10, idle=90), cpu_times(user=20, idle=80)],
        network={"eth0": network_counters(1_000, 2_000)},
        disks={"sda": disk_counters(10_000, 20_000)},
        busy={"sda": 1_000},
        processes=[],
    )
    monkeypatch.setattr(
        metrics, "time",
        SimpleNamespace(monotonic=lambda: frame.now, time=lambda: frame.wall),
    )
    monkeypatch.setattr(metrics.platform, "system", lambda: "Linux")
    # Keep the general fixture on the portable process reader on Linux too.
    # Tests for native procfs replace this with a temporary proc tree.
    monkeypatch.setattr(metrics, "PROC_DIRECTORY", Path("__fixture_no_procfs__"))
    monkeypatch.setattr(metrics, "_cpu_model", lambda: "Fixture CPU")
    monkeypatch.setattr(metrics, "_os_name", lambda: "Fixture Linux")
    monkeypatch.setattr(metrics, "_physical_disks", lambda counters: set(counters))
    monkeypatch.setattr(metrics, "_disk_busy_milliseconds", lambda devices: frame.busy)
    monkeypatch.setattr(metrics.shutil, "which", lambda command: None)
    monkeypatch.setattr(metrics.psutil, "cpu_times", lambda **kwargs: frame.cpu)
    monkeypatch.setattr(metrics.psutil, "cpu_count", lambda logical=True: 2 if logical else 1)
    monkeypatch.setattr(metrics.psutil, "cpu_freq", lambda: None)
    monkeypatch.setattr(metrics.psutil, "boot_time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(metrics.os, "getloadavg", lambda: (0.5, 0.25, 0.1), raising=False)
    monkeypatch.setattr(
        metrics.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            total=16_000, available=6_000, used=8_000, free=1_000,
            cached=5_000, buffers=1_000, percent=62.5,
        ),
    )
    monkeypatch.setattr(
        metrics.psutil, "swap_memory",
        lambda: SimpleNamespace(total=0, used=0, free=0, percent=0.0),
    )
    monkeypatch.setattr(metrics.psutil, "sensors_temperatures", lambda: {}, raising=False)
    monkeypatch.setattr(metrics.psutil, "net_io_counters", lambda **kwargs: frame.network)
    monkeypatch.setattr(metrics.psutil, "net_if_addrs", lambda: {})
    monkeypatch.setattr(metrics.psutil, "net_if_stats", lambda: {})
    monkeypatch.setattr(metrics.psutil, "disk_io_counters", lambda **kwargs: frame.disks)
    monkeypatch.setattr(metrics.psutil, "disk_partitions", lambda **kwargs: [])
    monkeypatch.setattr(
        metrics.psutil, "disk_usage",
        lambda path: SimpleNamespace(total=10_000, used=4_000, free=6_000, percent=40.0),
    )
    monkeypatch.setattr(metrics.psutil, "process_iter", lambda: iter(frame.processes))
    return frame
