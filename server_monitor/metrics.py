"""Collect genuine host measurements; unavailable measurements stay null."""

from __future__ import annotations

import csv
from concurrent.futures import Future, ThreadPoolExecutor
import io
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import time
from typing import Any

import psutil


PROC_DIRECTORY = Path("/proc")


def _rate(current: int | float, previous: int | float, elapsed: float) -> float:
    """Counter resets and missing sampling time must not create negative rates."""
    return max(0.0, (current - previous) / elapsed) if elapsed > 0 else 0.0


def _cpu_percent(current: Any, previous: Any) -> float:
    # Linux guest time is already included in user/nice; don't count it twice.
    fields = [name for name in current._fields if name not in {"guest", "guest_nice", "interrupt", "dpc"}]
    differences = {name: max(0.0, getattr(current, name) - getattr(previous, name)) for name in fields}
    total = sum(differences.values())
    idle = differences.get("idle", 0.0) + differences.get("iowait", 0.0)
    return round(min(100.0, max(0.0, (total - idle) / total * 100)), 1) if total else 0.0


def _number(value: str) -> float | None:
    try:
        result = float(value.strip())
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, AttributeError):
        return None


def _json_safe(value: Any) -> Any:
    """Broken/unsupported hardware readings cannot invalidate the JSON stream."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key.strip() in {"model name", "Hardware", "Processor"} and value.strip():
                return value.strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _os_name() -> str:
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME", platform.system())
    except OSError:
        return platform.system()


def _physical_disks(counters: dict[str, Any]) -> set[str]:
    """Use whole devices, excluding loop, partitions, and device-mapper duplicates."""
    if platform.system() != "Linux":
        return set(counters)
    devices: set[str] = set()
    try:
        for entry in Path("/sys/block").iterdir():
            if entry.name in counters and not entry.name.startswith(("loop", "ram", "zram", "dm-", "md")):
                devices.add(entry.name)
    except OSError:
        # diskstats has partitions too, so use conservative whole-device names.
        import re
        devices = {name for name in counters if re.fullmatch(r"(?:[svh]d[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)", name)}
    return devices


def _disk_busy_milliseconds(devices: set[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path("/proc/diskstats").read_text().splitlines():
            values = line.split()
            if len(values) >= 14 and values[2] in devices:
                result[values[2]] = int(values[12])
    except (OSError, ValueError):
        pass
    return result


class MetricsCollector:
    """One collector shared by every browser, sampled once each interval."""

    def __init__(self) -> None:
        self._previous_time = time.monotonic()
        self._previous_cpu = psutil.cpu_times(percpu=True)
        try:
            self._previous_disk = psutil.disk_io_counters(perdisk=True, nowrap=True) or {}
        except (OSError, RuntimeError):
            self._previous_disk = {}
        self._disk_devices = _physical_disks(self._previous_disk)
        self._previous_busy = _disk_busy_milliseconds(self._disk_devices)
        try:
            self._previous_network = psutil.net_io_counters(pernic=True, nowrap=True) or {}
        except OSError:
            self._previous_network = {}
        self._previous_processes: dict[int, tuple[float, float]] = {}
        self._gpu_command = shutil.which("nvidia-smi")
        self._gpu_retry_at = 0.0
        self._gpu_status_cache = "NVIDIA metrics unavailable."
        self._model = _cpu_model()
        self._hostname = socket.gethostname()
        self._os = _os_name()
        self._physical_cores = psutil.cpu_count(logical=False)
        self._logical_cores = psutil.cpu_count(logical=True) or len(self._previous_cpu)
        self._boot_time = psutil.boot_time()
        try:
            self._clock_ticks = os.sysconf("SC_CLK_TCK")
            self._page_size = os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, AttributeError):
            self._clock_ticks = self._page_size = None
        # Windows process/thread queries can take seconds. Keep them off the
        # core sampling path, with one bounded worker and explicit freshness.
        self._process_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monitor-processes") if platform.system() == "Windows" else None
        self._process_future: Future | None = None
        self._process_cache: dict[str, Any] | None = None
        self._process_cache_warnings: list[str] = []
        self._previous_process_clock: float | None = None
        self._closed = False

    def collect(self) -> dict[str, Any]:
        warnings: list[str] = []
        now = time.monotonic()
        timestamp = time.time()
        elapsed = max(0.0, now - self._previous_time)
        cpu_times = psutil.cpu_times(percpu=True)
        per_core = [
            _cpu_percent(current, previous)
            for current, previous in zip(cpu_times, self._previous_cpu)
        ]
        # Newly hot-plugged CPUs have no baseline yet.
        per_core.extend([0.0] * (len(cpu_times) - len(per_core)))
        cpu_percent = round(sum(per_core) / len(per_core), 1) if per_core else 0.0
        try:
            frequency = psutil.cpu_freq()
            frequency_mhz = frequency.current if frequency else None
        except (OSError, NotImplementedError, AttributeError):
            frequency_mhz = None
        try:
            load_average = list(os.getloadavg())
        except (OSError, AttributeError):
            load_average = None

        sensors = self._sensors(warnings)
        cpu_temperature = next(
            (sensor["current"] for sensor in sensors if any(word in sensor["label"].lower() for word in ("package", "cpu", "tctl", "tdie"))),
            None,
        )
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        addresses, network = self._network(elapsed, warnings)
        disk_io = self._disk_io(elapsed, warnings)
        if self._process_executor is not None:
            processes = self._background_processes(memory.total, warnings)
        else:
            processes = self._processes(elapsed, warnings, memory.total)
            processes["sampled_at"] = timestamp
        gpus, gpu_status = self._gpus()
        snapshot = {
            "timestamp": timestamp,
            "system": {
                "hostname": self._hostname,
                "os": self._os,
                "kernel": platform.release(),
                "architecture": platform.machine(),
                "boot_time": self._boot_time,
                "uptime_seconds": max(0.0, timestamp - self._boot_time),
                "addresses": addresses,
            },
            "cpu": {
                "percent": cpu_percent,
                "per_core": per_core,
                "logical_cores": len(cpu_times) or self._logical_cores,
                "physical_cores": self._physical_cores,
                "frequency_mhz": frequency_mhz,
                "model": self._model,
                "load_average": load_average,
                "temperature_celsius": cpu_temperature,
            },
            "memory": {
                "total_bytes": memory.total,
                "used_bytes": memory.total - memory.available,
                "available_bytes": memory.available,
                "cached_bytes": getattr(memory, "cached", 0),
                "buffers_bytes": getattr(memory, "buffers", 0),
                "percent": memory.percent,
                "swap": {"total_bytes": swap.total, "used_bytes": swap.used, "free_bytes": swap.free, "percent": swap.percent},
            },
            "disks": self._disks(warnings),
            "disk_io": disk_io,
            "network": network,
            "gpus": gpus,
            "gpu_status": gpu_status,
            "processes": processes,
            "sensors": sensors,
            "warnings": warnings,
        }
        self._previous_cpu = cpu_times
        self._previous_time = now
        return _json_safe(snapshot)

    def _background_processes(self, memory_total: int, warnings: list[str]) -> dict[str, Any]:
        if self._process_future is not None and self._process_future.done():
            try:
                self._process_cache, self._process_cache_warnings = self._process_future.result()
            except Exception:
                self._process_cache_warnings = ["Process details could not be refreshed."]
            self._process_future = None
        if self._process_future is None and not self._closed:
            self._process_future = self._process_executor.submit(self._collect_process_background, memory_total)
        warnings.extend(self._process_cache_warnings)
        if self._process_cache is None:
            warnings.append("Process details are being collected in the background.")
            try:
                total = len(psutil.pids())
            except OSError:
                total = None
                warnings.append("Process count could not be read.")
            return {"total": total, "threads": None, "top": [], "sampled_at": None}
        return self._process_cache

    def _collect_process_background(self, memory_total: int) -> tuple[dict[str, Any], list[str]]:
        now = time.monotonic()
        sampled_at = time.time()
        elapsed = max(0.0, now - self._previous_process_clock) if self._previous_process_clock is not None else 0.0
        warnings: list[str] = []
        processes = self._processes(elapsed, warnings, memory_total)
        processes["sampled_at"] = sampled_at
        self._previous_process_clock = now
        return processes, warnings

    def close(self) -> None:
        """Wait for the single process worker before the server leaves its lifespan."""
        self._closed = True
        if self._process_executor is not None:
            self._process_executor.shutdown(wait=True, cancel_futures=True)

    @staticmethod
    def _sensors(warnings: list[str]) -> list[dict[str, Any]]:
        try:
            readings = psutil.sensors_temperatures()
        except (AttributeError, NotImplementedError):
            return []
        except OSError:
            warnings.append("Temperature sensors could not be read.")
            return []
        return [
            {"label": f"{chip}: {entry.label or index + 1}", "current": entry.current, "high": entry.high, "critical": entry.critical}
            for chip, entries in readings.items()
            for index, entry in enumerate(entries)
        ]

    def _network(self, elapsed: float, warnings: list[str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
        try:
            interface_addresses = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            counters = psutil.net_io_counters(pernic=True, nowrap=True) or {}
        except OSError:
            warnings.append("Network interfaces could not be read.")
            self._previous_network = {}
            return [], {"sent_bytes_per_sec": 0.0, "recv_bytes_per_sec": 0.0, "sent_bytes": 0, "recv_bytes": 0, "interfaces": []}
        addresses: list[dict[str, str]] = []
        interfaces: list[dict[str, Any]] = []
        for name in sorted(set(interface_addresses) | set(counters)):
            ips = [entry.address for entry in interface_addresses.get(name, []) if entry.family in (socket.AF_INET, socket.AF_INET6)]
            mac = next((entry.address for entry in interface_addresses.get(name, []) if entry.family == psutil.AF_LINK), None)
            addresses.extend({"interface": name, "address": address} for address in ips)
            current = counters.get(name)
            previous = self._previous_network.get(name)
            sent_rate = _rate(current.bytes_sent, previous.bytes_sent, elapsed) if current and previous else 0.0
            recv_rate = _rate(current.bytes_recv, previous.bytes_recv, elapsed) if current and previous else 0.0
            stats_entry = stats.get(name)
            interfaces.append({
                "name": name,
                "address": next((ip for ip in ips if ":" not in ip), ips[0] if ips else None),
                "mac": mac,
                "is_up": stats_entry.isup if stats_entry else None,
                "speed_mbps": stats_entry.speed if stats_entry and stats_entry.speed > 0 else None,
                "sent_bytes_per_sec": sent_rate,
                "recv_bytes_per_sec": recv_rate,
                "sent_bytes": current.bytes_sent if current else 0,
                "recv_bytes": current.bytes_recv if current else 0,
            })
        self._previous_network = counters
        # Loopback is displayed separately but excluded from external traffic totals.
        external = [entry for entry in interfaces if entry["name"] not in {"lo", "lo0"}]
        return addresses, {
            key: sum(entry[key] for entry in external)
            for key in ("sent_bytes_per_sec", "recv_bytes_per_sec", "sent_bytes", "recv_bytes")
        } | {"interfaces": interfaces}

    def _disk_io(self, elapsed: float, warnings: list[str]) -> dict[str, Any]:
        try:
            counters = psutil.disk_io_counters(perdisk=True, nowrap=True) or {}
        except (OSError, RuntimeError):
            warnings.append("Disk I/O counters could not be read.")
            counters = {}
        devices = _physical_disks(counters)
        busy = _disk_busy_milliseconds(devices)
        read_rate = write_rate = 0.0
        read_bytes = write_bytes = 0
        busy_values: list[float] = []
        for device in devices:
            current = counters[device]
            previous = self._previous_disk.get(device)
            read_bytes += current.read_bytes
            write_bytes += current.write_bytes
            if previous:
                read_rate += _rate(current.read_bytes, previous.read_bytes, elapsed)
                write_rate += _rate(current.write_bytes, previous.write_bytes, elapsed)
            if device in busy and device in self._previous_busy and elapsed > 0:
                busy_values.append(min(100.0, _rate(busy[device], self._previous_busy[device], elapsed) / 10))
        self._previous_disk = counters
        self._previous_busy = busy
        self._disk_devices = devices
        return {
            "read_bytes_per_sec": read_rate,
            "write_bytes_per_sec": write_rate,
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
            "busy_percent": round(sum(busy_values) / len(busy_values), 1) if busy_values else None,
        }

    @staticmethod
    def _disks(warnings: list[str]) -> list[dict[str, Any]]:
        pseudo = {"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "securityfs", "debugfs", "tracefs", "pstore", "efivarfs", "mqueue", "hugetlbfs", "fusectl", "nsfs", "autofs", "binfmt_misc", "configfs", "ramfs", "bpf", "rpc_pipefs"}
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            partitions = psutil.disk_partitions(all=True)
        except OSError:
            warnings.append("Mounted filesystems could not be read.")
            partitions = []
        for partition in sorted(partitions, key=lambda entry: (entry.mountpoint != "/", entry.mountpoint)):
            if partition.fstype in pseudo or partition.mountpoint in seen or not os.path.isdir(partition.mountpoint):
                continue
            seen.add(partition.mountpoint)
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError):
                if len(warnings) < 8:
                    warnings.append(f"Cannot read filesystem {partition.mountpoint}.")
                continue
            result.append({
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "filesystem": partition.fstype,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "percent": usage.percent,
            })
        # Containers may expose only bind mounts; always attempt the root filesystem.
        if platform.system() == "Linux" and not any(entry["mountpoint"] == "/" for entry in result):
            try:
                usage = psutil.disk_usage("/")
                result.insert(0, {"device": "root", "mountpoint": "/", "filesystem": "unknown", "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free, "percent": usage.percent})
            except OSError:
                warnings.append("Cannot read root filesystem.")
        return result

    def _processes(self, elapsed: float, warnings: list[str], memory_total: int | None = None) -> dict[str, Any]:
        memory_total = memory_total if memory_total is not None else psutil.virtual_memory().total
        if self._clock_ticks and self._page_size and PROC_DIRECTORY.is_dir():
            try:
                return self._processes_proc(elapsed, warnings, memory_total)
            except OSError:
                warnings.append("Linux process files could not be read; using psutil.")
        top: list[dict[str, Any]] = []
        current_times: dict[int, tuple[float, float]] = {}
        process_handles: dict[int, Any] = {}
        total = threads = denied = 0
        for process in psutil.process_iter():
            total += 1
            try:
                with process.oneshot():
                    created = process.create_time()
                    cpu_times = process.cpu_times()
                    process_time = cpu_times.user + cpu_times.system
                    previous = self._previous_processes.get(process.pid)
                    cpu_percent = _rate(process_time, previous[1], elapsed) * 100 if previous and previous[0] == created else 0.0
                    current_times[process.pid] = (created, process_time)
                    threads += process.num_threads()
                    if process.pid == 0 and platform.system() == "Windows":
                        continue  # System Idle Process measures unused CPU time.
                    memory_bytes = process.memory_info().rss
                    process_handles[process.pid] = process
                    top.append({
                        "pid": process.pid,
                        "name": process.name(),
                        "cpu_percent": round(cpu_percent, 1),
                        "memory_percent": round(memory_bytes / memory_total * 100, 2) if memory_total else 0.0,
                        "memory_bytes": memory_bytes,
                        "status": "unknown",
                    })
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                denied += 1
        self._previous_processes = current_times
        if denied:
            warnings.append(f"Details unavailable for {denied} restricted processes; thread count may be partial.")
        top.sort(key=lambda entry: (entry["cpu_percent"], entry["memory_bytes"]), reverse=True)
        for entry in top[:10]:
            try:
                entry["status"] = process_handles[entry["pid"]].status()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                pass
        return {"total": total, "threads": threads, "top": top[:10]}

    def _processes_proc(self, elapsed: float, warnings: list[str], memory_total: int) -> dict[str, Any]:
        """One procfs read per task avoids slow per-process API calls on busy hosts."""
        statuses = {"R": "running", "S": "sleeping", "D": "disk-sleep", "Z": "zombie", "T": "stopped", "t": "tracing-stop", "X": "dead", "I": "idle", "W": "waking", "P": "parked"}
        with os.scandir(PROC_DIRECTORY) as entries:
            pids = [int(entry.name) for entry in entries if entry.name.isdecimal()]
        top: list[dict[str, Any]] = []
        current_times: dict[int, tuple[float, float]] = {}
        threads = denied = unreadable = 0
        for pid in pids:
            try:
                raw = (PROC_DIRECTORY / str(pid) / "stat").read_text(errors="replace")
                name_start = raw.index("(")
                name_end = raw.rindex(")")
                fields = raw[name_end + 2:].split()
                # Fields below begin at Linux stat field 3 (process state).
                process_time = (int(fields[11]) + int(fields[12])) / self._clock_ticks
                created = int(fields[19]) / self._clock_ticks
                previous = self._previous_processes.get(pid)
                cpu_percent = _rate(process_time, previous[1], elapsed) * 100 if previous and previous[0] == created else 0.0
                memory_bytes = max(0, int(fields[21]) * self._page_size)
                threads += int(fields[17])
                current_times[pid] = (created, process_time)
                top.append({"pid": pid, "name": raw[name_start + 1:name_end], "cpu_percent": round(cpu_percent, 1), "memory_percent": round(memory_bytes / memory_total * 100, 2) if memory_total else 0.0, "memory_bytes": memory_bytes, "status": statuses.get(fields[0], fields[0])})
            except FileNotFoundError:
                continue  # A process can exit between directory listing and sampling.
            except PermissionError:
                denied += 1
            except (OSError, ValueError, IndexError):
                unreadable += 1
        self._previous_processes = current_times
        if denied:
            warnings.append(f"Details unavailable for {denied} restricted processes; thread count may be partial.")
        if unreadable:
            warnings.append(f"Could not read details for {unreadable} processes.")
        top.sort(key=lambda entry: (entry["cpu_percent"], entry["memory_bytes"]), reverse=True)
        return {"total": len(pids), "threads": threads, "top": top[:10]}

    def _gpus(self) -> tuple[list[dict[str, Any]], str]:
        if not self._gpu_command:
            return [], "NVIDIA metrics unavailable: nvidia-smi is not installed or on PATH."
        if time.monotonic() < self._gpu_retry_at:
            return [], self._gpu_status_cache
        try:
            output = subprocess.run(
                [self._gpu_command, "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=0.75, check=True,
            ).stdout
        except subprocess.TimeoutExpired:
            return self._gpu_unavailable("NVIDIA metrics unavailable: nvidia-smi timed out.")
        except (OSError, subprocess.CalledProcessError):
            return self._gpu_unavailable("NVIDIA metrics unavailable: driver or device could not be queried.")
        gpus: list[dict[str, Any]] = []
        for row in csv.reader(io.StringIO(output), skipinitialspace=True):
            if len(row) != 7 or _number(row[0]) is None:
                continue
            index, name, utilization, used, total, temperature, power = row
            memory_used = _number(used)
            memory_total = _number(total)
            gpus.append({
                "index": int(float(index)),
                "name": name.strip(),
                "utilization_percent": _number(utilization),
                "memory_used_bytes": int(memory_used * 1024 * 1024) if memory_used is not None else None,
                "memory_total_bytes": int(memory_total * 1024 * 1024) if memory_total is not None else None,
                "memory_percent": round(memory_used / memory_total * 100, 1) if memory_used is not None and memory_total else None,
                "temperature_celsius": _number(temperature),
                "power_watts": _number(power),
            })
        return gpus, "NVIDIA metrics available." if gpus else "No accessible NVIDIA GPU was detected."

    def _gpu_unavailable(self, status: str) -> tuple[list[dict[str, Any]], str]:
        self._gpu_retry_at = time.monotonic() + 5
        self._gpu_status_cache = status
        return [], status


def history_entry(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": snapshot["timestamp"],
        "cpu": snapshot["cpu"]["percent"],
        "memory": snapshot["memory"]["percent"],
        "gpu": snapshot["gpus"][0]["utilization_percent"] if snapshot["gpus"] else None,
        "disk": snapshot["disk_io"]["busy_percent"],
        "network_recv": snapshot["network"]["recv_bytes_per_sec"],
        "network_sent": snapshot["network"]["sent_bytes_per_sec"],
        "disk_read": snapshot["disk_io"]["read_bytes_per_sec"],
        "disk_write": snapshot["disk_io"]["write_bytes_per_sec"],
    }
