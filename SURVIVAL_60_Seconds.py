#!/usr/bin/env python3
"""SURVIVAL - 60 Seconds - Stress Test: Burst Mode. Designed by Jarvis AGI and Josh."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import multiprocessing as mp
import os
import platform
import queue
import random
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.3.1"
LOCAL_REVISION = f"{VERSION} local repaired edition (2026-10-31)"
CREATOR = "Jarvis AGI"

def cpu_worker(stop_event: mp.Event, operations: mp.Value, deadline: float) -> None:
    payload = os.urandom(1024 * 1024)
    local_ops = 0
    while not stop_event.is_set() and time.perf_counter() < deadline:
        hashlib.sha512(payload).digest()
        local_ops += 1
        if local_ops % 100 == 0:
            with operations.get_lock():
                operations.value += 100
            local_ops = 0
    if local_ops:
        with operations.get_lock():
            operations.value += local_ops


def ram_worker(stop: threading.Event, target_bytes: int, result: queue.Queue, deadline: float) -> None:
    blocks: list[tuple[bytearray, int]] = []
    errors: list[str] = []
    block_size = 64 * 1024 * 1024
    try:
        remaining = target_bytes
        index = 0
        while remaining > 0 and not stop.is_set() and time.perf_counter() < deadline:
            size = min(block_size, remaining)
            pattern = (index * 37 + 11) % 251
            block = bytearray([pattern]) * size
            blocks.append((block, pattern))
            remaining -= size
            index += 1

        passes = 0
        while not stop.is_set() and time.perf_counter() < deadline:
            completed_pass = True
            for block_index, (block, pattern) in enumerate(blocks):
                # Sample every 4 KiB plus the final byte. This is deliberately a
                # short smoke test; MemTest86 remains the proper long-form test.
                for pos in range(0, len(block), 4096):
                    if pos % (1024 * 1024) == 0 and (stop.is_set() or time.perf_counter() >= deadline):
                        completed_pass = False
                        break
                    if block[pos] != pattern:
                        errors.append(f"Block {block_index}, offset {pos}: expected {pattern}, got {block[pos]}")
                        stop.set()
                        break
                if block and block[-1] != pattern:
                    errors.append(f"Block {block_index}, final byte mismatch")
                    stop.set()
                if stop.is_set() or not completed_pass:
                    completed_pass = False
                    break
            if completed_pass:
                passes += 1
        result.put({"allocated_bytes": sum(len(b) for b, _ in blocks), "passes": passes, "errors": errors})
    except MemoryError:
        result.put({"allocated_bytes": sum(len(b) for b, _ in blocks), "passes": 0,
                    "errors": ["Memory allocation failed before the safe target was reached."]})
        stop.set()
    except Exception as exc:  # noqa: BLE001
        result.put({"allocated_bytes": sum(len(b) for b, _ in blocks), "passes": 0,
                    "errors": [f"RAM worker error: {exc}"]})
        stop.set()


def gpu_worker(stop: threading.Event, result: queue.Queue, deadline: float) -> None:
    started = time.perf_counter()
    output = {"available": False, "iterations": 0, "tflops_estimate": 0.0, "errors": []}
    try:
        import torch

        output["torch_version"] = torch.__version__
        output["cuda_runtime"] = torch.version.cuda
        if not torch.cuda.is_available():
            output["errors"].append("PyTorch cannot access CUDA. Check the NVIDIA driver and CUDA-enabled PyTorch build.")
            stop.set()
            result.put(output)
            return

        output["available"] = True
        output["device"] = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        output["vram_bytes"] = props.total_memory
        n = 8192 if props.total_memory >= 20 * 1024**3 else 6144 if props.total_memory >= 10 * 1024**3 else 4096
        output["matrix_size"] = n
        if stop.is_set() or time.perf_counter() >= deadline:
            return

        torch.backends.cuda.matmul.allow_tf32 = True
        a = torch.randn((n, n), device="cuda", dtype=torch.float16)
        b = torch.randn((n, n), device="cuda", dtype=torch.float16)
        for _ in range(3):
            if stop.is_set() or time.perf_counter() >= deadline:
                return
            _ = a @ b
        torch.cuda.synchronize()
        measured_start = time.perf_counter()
        iterations = 0
        while not stop.is_set() and time.perf_counter() < deadline:
            c = a @ b
            iterations += 1
            if iterations % 8 == 0:
                torch.cuda.synchronize()
                # Force a tiny readback, catching many execution/data-path faults.
                if not bool(torch.isfinite(c[0, 0]).item()):
                    raise RuntimeError("CUDA matrix output became non-finite")
        torch.cuda.synchronize()
        elapsed = max(time.perf_counter() - measured_start, 0.001)
        output["iterations"] = iterations
        output["tflops_estimate"] = round((2 * (n**3) * iterations) / elapsed / 1e12, 2)
        output["max_allocated_vram_bytes"] = torch.cuda.max_memory_allocated(0)
    except Exception as exc:  # noqa: BLE001
        output["errors"].append(f"GPU worker error: {exc}")
        stop.set()
    finally:
        output["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        result.put(output)


def nvidia_metrics() -> dict[str, object]:
    fields = ["name", "driver_version", "temperature.gpu", "utilization.gpu",
              "memory.used", "memory.total", "power.draw", "power.limit", "clocks.sm"]
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, check=True,
        )
        values = [v.strip() for v in proc.stdout.splitlines()[0].split(",")]
        raw = dict(zip(fields, values))
        return {
            "gpu_name": raw["name"], "driver_version": raw["driver_version"],
            "gpu_temp_c": float(raw["temperature.gpu"]), "gpu_util_percent": float(raw["utilization.gpu"]),
            "gpu_memory_used_mb": float(raw["memory.used"]), "gpu_memory_total_mb": float(raw["memory.total"]),
            "gpu_power_w": float(raw["power.draw"]), "gpu_power_limit_w": float(raw["power.limit"]),
            "gpu_clock_mhz": float(raw["clocks.sm"]),
        }
    except Exception:
        return {}


def whea_error_count(since_utc: str) -> int | None:
    if platform.system() != "Windows":
        return None
    command = (
        "try { Get-WinEvent -ListLog System -ErrorAction Stop | Out-Null; "
        f"$s=[datetime]::Parse('{since_utc}').ToLocalTime(); "
        "@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WHEA-Logger';"
        "StartTime=$s} -ErrorAction SilentlyContinue).Count } catch { exit 1 }"
    )
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                              text=True, timeout=4, check=True)
        return int(proc.stdout.strip())
    except Exception:
        return None


def cpu_temperature_c() -> float | None:
    """Read CPU package temperature when Libre/OpenHardwareMonitor exposes WMI."""
    if platform.system() != "Windows":
        return None
    command = (
        "$v=$null; "
        "$v=(Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction SilentlyContinue | "
        "Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU Package'} | "
        "Select-Object -First 1 -ExpandProperty Value); "
        "if($null -eq $v){$v=(Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor -ErrorAction SilentlyContinue | "
        "Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU Package'} | "
        "Select-Object -First 1 -ExpandProperty Value)}; if($null -ne $v){$v}"
    )
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                              text=True, timeout=8, check=True)
        value = float(proc.stdout.strip()) if proc.stdout.strip() else None
        return value if value is not None and math.isfinite(value) and value >= 0 else None
    except Exception:
        return None


def gpu_preflight() -> dict[str, object]:
    """Check CUDA and safety sensors before any stress workers start."""
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA-enabled PyTorch and an accessible NVIDIA GPU are required.")
    torch.cuda.get_device_properties(0)
    row = nvidia_metrics()
    problem = gpu_sensor_problem(row)
    if problem:
        raise RuntimeError(problem)
    return row


def gpu_sensor_problem(row: dict[str, object]) -> str | None:
    for key in ("gpu_temp_c", "gpu_power_w", "gpu_power_limit_w"):
        value = row.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return f"Required NVIDIA safety telemetry is unavailable or invalid: {key}."
    if float(row["gpu_power_limit_w"]) <= 0:
        return "NVIDIA reported an invalid power limit."
    if float(row["gpu_temp_c"]) >= 87:
        return "GPU temperature reached the 87 C hard limit."
    power, limit = float(row["gpu_power_w"]), float(row["gpu_power_limit_w"])
    if power > max(limit * 1.10, limit + 20):
        return "GPU power exceeded the safety limit."
    return None


def deadline_watchdog(parent_pid: int, deadline: float, stop: mp.Event, finished: mp.Event) -> None:
    """Independent process: stop work at deadline; kill a hung parent after 15s cleanup grace."""
    if finished.wait(max(0.0, deadline - time.perf_counter())):
        return
    stop.set()
    if not finished.wait(15.0):
        os.kill(parent_pid, signal.SIGTERM)


def safe_number(values: list[dict[str, object]], key: str, fn) -> float | None:
    nums = [float(row[key]) for row in values if row.get(key) not in (None, "")]
    return round(fn(nums), 2) if nums else None


def matrix_success_screen(score: int) -> None:
    """Short, low-load success animation after all stress workloads have stopped."""
    try:
        if platform.system() == "Windows":
            os.system("")  # Enable ANSI processing in modern Windows terminals.
        width, terminal_height = shutil.get_terminal_size((90, 28))
        width = min(max(width, 60), 120)
        height = min(max(terminal_height - 4, 16), 30)
        alphabet = "01ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        deadline = time.perf_counter() + 3.5
        while time.perf_counter() < deadline:
            frame = []
            for _ in range(height):
                line = "".join(random.choice(alphabet + "      ") for _ in range(width))
                frame.append(f"\033[32m{line}\033[0m")
            message = "WELCOME TO THE MATRIX"
            score_line = f"SURVIVAL SCORE: {score}/100"
            middle = height // 2
            frame[middle] = f"\033[1;92m{message.center(width)}\033[0m"
            frame[middle + 1] = f"\033[1;92m{score_line.center(width)}\033[0m"
            print("\033[2J\033[H" + "\n".join(frame), end="", flush=True)
            time.sleep(0.12)
        print("\033[2J\033[H\033[1;92m" + "WELCOME TO THE MATRIX".center(width) + "\033[0m")
        print(f"\033[92m{'SURVIVAL COMPLETE - AI DEV SCORE ' + str(score) + '/100':^{width}}\033[0m\n")
    except Exception:
        print("\nWELCOME TO THE MATRIX")
        print(f"SURVIVAL COMPLETE - AI DEV SCORE {score}/100\n")


def cpu_model_name() -> str:
    if platform.system() == "Windows":
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)"],
                capture_output=True, text=True, timeout=10, check=True,
            )
            if proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
    return platform.processor() or "Unknown CPU"


def ram_capacity_details(usable_bytes: int) -> dict[str, object]:
    """Keep physically installed capacity separate from memory Windows can use."""
    installed_bytes = None
    if platform.system() == "Windows":
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | Measure-Object -Property Capacity -Sum).Sum"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            measured = int(proc.stdout.strip())
            if measured > 0 and measured >= usable_bytes:
                installed_bytes = measured
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return {
        "installed_ram_bytes": installed_bytes,
        "installed_ram_gb": round(installed_bytes / 1024**3, 2) if installed_bytes is not None else None,
        "usable_ram_bytes": usable_bytes,
        "usable_ram_gb": round(usable_bytes / 1024**3, 2),
        "total_ram_gb": round((installed_bytes if installed_bytes is not None else usable_bytes) / 1024**3, 2),
        "ram_capacity_basis": "installed physical modules" if installed_bytes is not None else "OS-usable fallback; installed capacity unavailable",
    }


def ai_dev_score(summary: dict[str, object]) -> dict[str, object]:
    """Transparent heuristic, not an industry-certified benchmark."""
    import math
    if not str(summary.get("status", "")).startswith("PASS") or summary.get("safety_abort"):
        return {"score": None, "tier": "UNSCORED — failed or incomplete test",
                "reference_score": 65, "reference_label": "Typical AI developer workstation baseline",
                "components": {}, "component_maximums": {"CPU": 20, "RAM": 20, "GPU": 50, "Stability": 10},
                "method_note": "Failed, aborted and incomplete tests are unscored. No hardware capability conclusion follows from this result."}

    system = summary["system"]
    cpu = summary["cpu"]
    ram = summary["ram"]
    gpu = summary["gpu"]
    telemetry = summary["telemetry"]

    logical = float(system.get("logical_cpu_count") or 0)
    avg_cpu = float(cpu.get("average_util_percent") or 0)
    cpu_capability = min(15.0, 15.0 * math.sqrt(max(logical, 0) / 32.0))
    cpu_load = min(5.0, 5.0 * avg_cpu / 85.0)
    cpu_points = cpu_capability + cpu_load

    total_ram = float(system.get("total_ram_gb") or 0)
    if total_ram > 0:
        ram_capacity = min(17.0, max(0.0, 6.0 + 5.0 * math.log2(total_ram / 16.0)))
    else:
        ram_capacity = 0.0
    ram_integrity = 3.0 if not ram.get("errors") and int(ram.get("passes", 0)) > 0 else 0.0
    ram_points = ram_capacity + ram_integrity

    vram_gb = float(telemetry.get("gpu_total_vram_mb") or 0) / 1024.0
    throughput = float(gpu.get("tflops_estimate") or 0)
    gpu_vram = min(25.0, 25.0 * vram_gb / 24.0)
    gpu_compute = min(20.0, 20.0 * math.sqrt(max(throughput, 0) / 150.0))
    gpu_ready = 5.0 if gpu.get("available") and not gpu.get("errors") else 0.0
    gpu_points = gpu_vram + gpu_compute + gpu_ready

    requested = float(summary.get("requested_seconds") or 60)
    actual = float(summary.get("actual_seconds") or 0)
    stability = 2.0 if actual >= requested * 0.95 else 0.0
    stability += 4.0 if summary.get("whea_errors_during_test") in (0, None) else 0.0
    stability += 2.0 if (telemetry.get("max_gpu_temp_c") or 0) < 87 else 0.0
    stability += 2.0 if not summary.get("fail_reasons") else 0.0

    total = round(min(100.0, cpu_points + ram_points + gpu_points + stability))
    if total >= 90:
        tier = "AI production powerhouse"
    elif total >= 80:
        tier = "High-end local AI development"
    elif total >= 65:
        tier = "Mainstream AI developer workstation"
    elif total >= 50:
        tier = "Entry-level local AI development"
    else:
        tier = "Limited for modern local AI workloads"
    return {
        "score": total,
        "tier": tier,
        "reference_score": 65,
        "reference_label": "Typical AI developer workstation baseline",
        "components": {
            "CPU": round(cpu_points, 1), "RAM": round(ram_points, 1),
            "GPU": round(gpu_points, 1), "Stability": round(stability, 1),
        },
        "component_maximums": {"CPU": 20, "RAM": 20, "GPU": 50, "Stability": 10},
        "method_note": (
            "Internal comparative heuristic: CPU 20%, RAM 20%, CUDA GPU/VRAM 50%, stability 10%. "
            "The 65-point reference is a declared engineering baseline, not a surveyed industry average."
        ),
    }


def analyze_bottlenecks(summary: dict[str, object], samples: list[dict[str, object]]) -> dict[str, object]:
    cpu_avg = float(summary["cpu"].get("average_util_percent") or 0)
    gpu_avg = float(summary["telemetry"].get("average_gpu_util_percent") or 0)
    gpu_temp = float(summary["telemetry"].get("max_gpu_temp_c") or 0)
    vram_total = float(summary["telemetry"].get("gpu_total_vram_mb") or 0)
    vram_used = float(summary["telemetry"].get("max_gpu_memory_used_mb") or 0)
    total_ram = float(summary["system"].get("total_ram_gb") or 0)
    available_values = [float(row["ram_available_gb"]) for row in samples if row.get("ram_available_gb") not in (None, "")]
    min_available = min(available_values) if available_values else None
    findings: list[dict[str, str]] = []

    def add(area: str, severity: str, evidence: str, action: str) -> None:
        findings.append({"area": area, "severity": severity, "evidence": evidence, "recommended_action": action})

    if not summary["gpu"].get("available"):
        add("CUDA / driver", "critical", "PyTorch could not complete a CUDA tensor workload.",
            "Install a current NVIDIA driver and a CUDA-enabled PyTorch build, then rerun SURVIVAL.")
    if summary.get("whea_errors_during_test") not in (0, None):
        add("Hardware stability", "critical", f"Windows reported {summary['whea_errors_during_test']} new WHEA error(s) during the test.",
            "Return clocks to stock, check RAM seating and BIOS settings, then inspect the matching Windows Event Viewer entries.")
    if gpu_temp >= 87:
        add("GPU cooling", "critical", f"The GPU reached the {gpu_temp:.0f} C safety-stop region.",
            "Stop production loads until airflow, fans, dust, thermal interface and ambient temperature are checked.")
    elif gpu_temp >= 85:
        add("GPU cooling", "high", f"Peak GPU temperature was {gpu_temp:.0f} C.",
            "Improve case airflow or fan curves and retest before a long AI training or inference job.")

    if min_available is not None and total_ram > 0:
        reserve = max(2.0, total_ram * 0.10)
        if min_available < reserve:
            add("System RAM", "high", f"Available RAM fell to {min_available:.1f} GB; the safety reserve is {reserve:.1f} GB.",
                "Add RAM or reduce concurrent models, datasets and applications; watch paging during the real workload.")
    if summary["system"].get("installed_ram_gb") is not None and total_ram < 32:
        add("System RAM capacity", "medium", f"Installed RAM is {total_ram:.0f} GiB, below the 32 GiB SURVIVAL developer reference.",
            "Use at least 32 GB for routine local AI development; consider 64 GB for larger datasets and multiple services.")

    if vram_total:
        vram_ratio = vram_used / vram_total
        if vram_ratio >= 0.90:
            add("VRAM pressure", "high", f"Peak VRAM use reached {vram_ratio * 100:.0f}% ({vram_used/1024:.1f}/{vram_total/1024:.1f} GB).",
                "Reduce batch or context size, use quantisation or offloading, or move to a GPU with more VRAM.")
        elif vram_total < 12 * 1024:
            add("VRAM capacity", "medium", f"Installed VRAM is {vram_total/1024:.1f} GB, below the 12 GB SURVIVAL reference.",
                "Prefer smaller or quantised models; for larger local models, prioritise a GPU with more VRAM.")

    if summary["gpu"].get("available") and not any(item["severity"] == "critical" for item in findings):
        if gpu_avg >= 85:
            add("GPU compute", "informational", f"CUDA GPU averaged {gpu_avg:.0f}% utilisation; it was the saturated resource as intended.",
                "This is healthy for a tensor burst. A faster GPU is the direct route to lower compute time if more speed is required.")
        elif cpu_avg >= 85 and gpu_avg < 70:
            add("CPU / data pipeline", "high", f"CPU averaged {cpu_avg:.0f}% while GPU averaged only {gpu_avg:.0f}%.",
                "Check data loading, preprocessing, worker counts and CPU-to-GPU transfers; the GPU may be waiting for the CPU.")
        elif gpu_avg < 60:
            add("GPU dispatch", "medium", f"GPU averaged only {gpu_avg:.0f}% during the CUDA workload.",
                "Check power mode, driver state, PyTorch CUDA build and background contention; rerun after a clean restart.")

    serious = [item for item in findings if item["severity"] in {"critical", "high"}]
    primary = serious[0]["area"] if serious else findings[0]["area"] if findings else "No clear bottleneck detected"
    return {
        "primary_bottleneck": primary,
        "findings": findings,
        "scope_note": "Synthetic CPU/RAM/CUDA telemetry cannot diagnose storage, network, model architecture or application-code bottlenecks. Confirm findings with the real AI workload.",
    }


def svg_line_chart(rows: list[dict[str, object]], series: list[tuple[str, str, str]], title: str,
                   unit: str, fixed_max: float | None = None) -> str:
    width, height = 760, 250
    left, right, top, bottom = 55, 18, 35, 38
    values = [float(row[key]) for row in rows for key, _, _ in series if row.get(key) not in (None, "")]
    if not values:
        return f"<section class='chart'><h2>{html.escape(title)}</h2><p>Telemetry unavailable.</p></section>"
    lo = 0.0
    hi = fixed_max if fixed_max is not None else max(values) * 1.1
    hi = max(hi, 1.0)
    plot_w, plot_h = width - left - right, height - top - bottom
    count = max(len(rows) - 1, 1)
    grid = []
    for i in range(5):
        y = top + plot_h * i / 4
        value = hi * (4 - i) / 4
        grid.append(f"<line x1='{left}' y1='{y:.1f}' x2='{width-right}' y2='{y:.1f}' class='grid'/><text x='{left-8}' y='{y+4:.1f}' text-anchor='end'>{value:.0f}</text>")
    paths, legend = [], []
    for key, label, color in series:
        points = []
        for i, row in enumerate(rows):
            if row.get(key) in (None, ""):
                continue
            x = left + plot_w * i / count
            y = top + plot_h * (1 - min(max(float(row[key]), lo), hi) / hi)
            points.append(f"{x:.1f},{y:.1f}")
        if points:
            paths.append(f"<polyline points='{' '.join(points)}' fill='none' stroke='{color}' stroke-width='3'/>")
            legend.append(f"<span><b style='background:{color}'></b>{html.escape(label)}</span>")
    return (
        f"<section class='chart'><h2>{html.escape(title)}</h2><div class='legend'>{''.join(legend)}</div>"
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{html.escape(title)}'>{''.join(grid)}{''.join(paths)}"
        f"<text x='{width/2}' y='{height-8}' text-anchor='middle'>Elapsed time (seconds)</text>"
        f"<text transform='translate(14 {height/2}) rotate(-90)' text-anchor='middle'>{html.escape(unit)}</text></svg></section>"
    )


def write_html_report(out_dir: Path, summary: dict[str, object], samples: list[dict[str, object]]) -> None:
    score = summary["ai_dev_score"]
    score_html = f"{score['score']}<small>/100</small>" if score['score'] is not None else "<small>UNSCORED</small>"
    components = score["components"]
    maximums = score["component_maximums"]
    bars = "".join(
        f"<div class='score-row'><span>{html.escape(name)}</span><div><i style='width:{value/maximums[name]*100:.1f}%'></i></div><strong>{value}/{maximums[name]}</strong></div>"
        for name, value in components.items()
    )
    cpu_gpu_chart = svg_line_chart(samples, [("cpu_percent", "CPU", "#2563eb"),
                                                    ("gpu_util_percent", "CUDA GPU", "#16a34a")],
                                       "Compute utilisation", "Utilisation (%)", 100)
    ram_chart = svg_line_chart(samples, [("ram_used_gb", "RAM used", "#7c3aed"),
                                               ("ram_available_gb", "RAM available", "#f59e0b")],
                                  "System RAM during test", "Memory (GB)")
    vram_chart = svg_line_chart(samples, [("gpu_memory_used_mb", "VRAM used", "#dc2626"),
                                                ("gpu_memory_total_mb", "VRAM installed", "#64748b")],
                                   "GPU memory", "VRAM (MB)")
    temp_chart = svg_line_chart(samples, [("gpu_temp_c", "GPU temperature", "#ea580c")],
                                   "GPU thermal profile", "Temperature (C)", 100)
    power_chart = svg_line_chart(samples, [("gpu_power_w", "GPU power", "#0891b2")],
                                    "GPU power draw", "Power (W)")
    reasons = "".join(f"<li>{html.escape(str(item))}</li>" for item in summary.get("fail_reasons", [])) or "<li>None detected.</li>"
    warnings = "".join(f"<li>{html.escape(str(item))}</li>" for item in summary.get("warnings", [])) or "<li>None.</li>"
    bottleneck = summary["bottleneck_analysis"]
    bottleneck_rows = "".join(
        f"<tr><td><strong>{html.escape(item['area'])}</strong><br><small>{html.escape(item['severity'])}</small></td><td>{html.escape(item['evidence'])}</td><td>{html.escape(item['recommended_action'])}</td></tr>"
        for item in bottleneck["findings"]
    ) or "<tr><td colspan='3'>No clear bottleneck detected in this burst.</td></tr>"
    success_banner = "<section class='matrix'><strong>WELCOME TO THE MATRIX</strong><span>SURVIVAL completed successfully.</span></section>" if str(summary["status"]).startswith("PASS") and not summary.get("safety_abort") else ""
    doc = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>SURVIVAL - 60 Seconds - Burst Mode</title><style>
:root{{--ink:#101828;--muted:#667085;--line:#e4e7ec;--panel:#fff;--bg:#f6f8fb;--accent:#2563eb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}}
main{{max-width:1200px;margin:auto;padding:28px}}header,.chart,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px}}
header{{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:center}}h1{{margin:0 0 4px;font-size:28px}}h2{{margin:0 0 12px;font-size:17px}}p{{margin:5px 0;color:var(--muted)}}
.score{{font-size:52px;font-weight:800;color:var(--accent);text-align:center}}.score small{{font-size:18px;color:var(--muted)}}.score p{{font-size:14px;font-weight:500;max-width:260px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}.card{{background:#101828;color:white;border-radius:12px;padding:16px}}.card span{{display:block;color:#98a2b3;font-size:12px}}.card strong{{font-size:18px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}}svg{{width:100%;height:auto}}svg text{{font-size:11px;fill:#667085}}.grid{{stroke:#e4e7ec;stroke-width:1}}.legend{{display:flex;gap:18px;margin-bottom:4px;color:var(--muted);font-size:12px}}.legend b{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}}
.score-row{{display:grid;grid-template-columns:80px 1fr 72px;gap:10px;align-items:center;margin:10px 0}}.score-row div{{height:12px;background:#eaecf0;border-radius:8px;overflow:hidden}}.score-row i{{display:block;height:100%;background:#2563eb}}ul{{margin:4px 0;padding-left:20px}}
.bottlenecks{{width:100%;border-collapse:collapse}}.bottlenecks th,.bottlenecks td{{border:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}.bottlenecks th{{background:#101828;color:white}}small{{color:var(--muted)}}
.matrix{{margin:16px 0;background:#020b05;color:#39ff88;border:1px solid #16a34a;border-radius:14px;padding:22px;text-align:center;letter-spacing:3px}}.matrix strong{{display:block;font-size:25px}}.matrix span{{display:block;color:#86efac;letter-spacing:1px}}
.note{{font-size:12px;color:var(--muted)}}@media(max-width:800px){{.cards,.grid2{{grid-template-columns:1fr}}header{{grid-template-columns:1fr}}}}
</style></head><body><main>
<header><div><h1>SURVIVAL - 60 Seconds</h1><p><strong>Stress Test: Burst Mode</strong></p><p>Creator: {CREATOR}</p><p>Designed by Jarvis AGI and Josh · {LOCAL_REVISION}</p><p>{html.escape(str(summary['system']['cpu_name']))}</p><p>{html.escape(str(summary['gpu'].get('device', 'CUDA GPU unavailable')))}</p></div><div class='score'>{score_html}<p>{html.escape(str(score['tier']))}</p></div></header>
{success_banner}
<div class='cards'><div class='card'><span>Test result</span><strong>{html.escape(str(summary['status']))}</strong></div><div class='card'><span>Installed / OS-usable RAM</span><strong>{summary['system'].get('installed_ram_gb') if summary['system'].get('installed_ram_gb') is not None else 'Unknown'} / {summary['system']['usable_ram_gb']} GiB</strong></div><div class='card'><span>Peak CPU</span><strong>{summary['cpu']['peak_util_percent']}%</strong></div><div class='card'><span>Peak GPU</span><strong>{summary['telemetry']['peak_gpu_util_percent']}%</strong></div></div>
<div class='grid2'>{cpu_gpu_chart}{ram_chart}{vram_chart}{temp_chart}{power_chart}<section class='panel'><h2>AI Dev Readiness Score</h2>{bars}<p><strong>Reference:</strong> {score['reference_score']}/100 - {html.escape(str(score['reference_label']))}</p><p class='note'>{html.escape(str(score['method_note']))}</p></section></div>
<div class='grid2'><section class='panel'><h2>Faults</h2><ul>{reasons}</ul></section><section class='panel'><h2>Warnings</h2><ul>{warnings}</ul></section></div>
<section class='panel' style='margin-top:16px'><h2>Bottleneck analysis: {html.escape(str(bottleneck['primary_bottleneck']))}</h2><table class='bottlenecks'><thead><tr><th>Area</th><th>Evidence</th><th>Recommended action</th></tr></thead><tbody>{bottleneck_rows}</tbody></table><p class='note'>{html.escape(str(bottleneck['scope_note']))}</p></section>
<section class='panel'><h2>Beta risk warning</h2><p><strong>USE ENTIRELY AT YOUR OWN RISK.</strong> Hardware stress testing may cause crashes, overheating, data loss or hardware damage. To the fullest extent permitted by law, the designers and distributors accept no liability arising from use of this beta software - including, bluntly, if your computer explodes. This does not exclude liability that cannot legally be excluded.</p></section>
<p class='note'>Generated from every recorded telemetry sample. This 60-second smoke test does not certify long-term hardware stability.</p>
</main></body></html>"""
    (out_dir / "SURVIVAL_Report.html").write_text(doc, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="SURVIVAL - 60 Seconds - AI Stress Test: Burst Mode")
    parser.add_argument("--seconds", type=int, default=60, help="Fixed at 60 seconds for safety")
    parser.add_argument("--ram-percent", type=int, default=25)
    parser.add_argument("--ram-cap-gb", type=float, default=8.0)
    parser.add_argument("--output-dir", type=Path, help="New run directory for reports (must not exist)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the report or success animation")
    args = parser.parse_args()
    if args.seconds != 60:
        print("SAFETY ABORT: SURVIVAL is hard-limited to exactly 60 seconds. Overnight testing is prohibited.")
        print("No workload was started. This argument error does not establish hardware capability.")
        return 2
    if not 1 <= args.ram_percent <= 100 or not math.isfinite(args.ram_cap_gb) or args.ram_cap_gb <= 0:
        parser.error("--ram-percent must be 1..100 and --ram-cap-gb must be positive and finite")

    try:
        import psutil
    except ImportError:
        print("ERROR: psutil is missing. Run Setup_SURVIVAL.cmd to prepare the isolated environment.")
        return 2

    try:
        preflight_gpu = gpu_preflight()
    except Exception as exc:
        print(f"PREFLIGHT ABORT: {exc}. No stress workload was started.")
        return 2
    initial_cpu_temp = cpu_temperature_c()
    if initial_cpu_temp is not None and initial_cpu_temp >= 95:
        print("PREFLIGHT ABORT: CPU temperature reached the 95 C hard limit. No stress workload was started.")
        return 2
    if initial_cpu_temp is None:
        print("WARNING: CPU package temperature is unavailable; CPU thermal protection cannot be verified by this test.")
    cpu_name = cpu_model_name()
    ram_details = ram_capacity_details(psutil.virtual_memory().total)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    out_dir = args.output_dir or Path.cwd() / "outputs" / "SURVIVAL_Test_Records" / timestamp
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    started_utc = datetime.now(timezone.utc).isoformat()
    stop_threads = mp.Event()
    stop_processes = stop_threads
    watchdog_finished = mp.Event()
    cpu_ops = mp.Value("Q", 0)
    ram_results: queue.Queue = queue.Queue()
    gpu_results: queue.Queue = queue.Queue()

    available = psutil.virtual_memory().available
    if available < 2 * 1024**3 + 256 * 1024**2:
        print("PREFLIGHT ABORT: Insufficient RAM to preserve a 2 GiB reserve. No stress workload was started.")
        return 2
    target_ram = min(int(available * args.ram_percent / 100), int(args.ram_cap_gb * 1024**3))
    target_ram = max(min(target_ram, max(available - 2 * 1024**3, 256 * 1024**2)), 256 * 1024**2)
    cpu_workers = max(1, (os.cpu_count() or 2) - 1)

    print("\nSURVIVAL - 60 SECONDS - STRESS TEST: BURST MODE")
    print(f"Creator: {CREATOR}")
    print("Designed by Jarvis AGI and Josh")
    print("BETA SOFTWARE - USE ENTIRELY AT YOUR OWN RISK")
    print(f"Record folder: {out_dir}")
    print(f"CPU workers: {cpu_workers} | RAM target: {target_ram / 1024**3:.2f} GB")
    print("Press Ctrl+C to abort. Thermal safety stops: 87 C GPU / 95 C CPU when CPU telemetry is available.\n")

    began = time.perf_counter()
    deadline = began + args.seconds
    processes = [mp.Process(target=cpu_worker, args=(stop_processes, cpu_ops, deadline), daemon=True)
                 for _ in range(cpu_workers)]
    ram_thread = threading.Thread(target=ram_worker, args=(stop_threads, target_ram, ram_results, deadline), daemon=True)
    gpu_thread = threading.Thread(target=gpu_worker, args=(stop_threads, gpu_results, deadline), daemon=True)
    watchdog = mp.Process(target=deadline_watchdog, args=(os.getpid(), deadline, stop_threads, watchdog_finished), daemon=True)

    samples: list[dict[str, object]] = []
    safety_abort_reason: str | None = None
    whea_before = 0
    next_cpu_temp_check = 5.0
    next_whea_check = 10.0
    last_cpu_temp = initial_cpu_temp
    forced_cpu_stops = 0
    load_finished_at = began
    try:
        watchdog.start()
        for process in processes:
            process.start()
        ram_thread.start()
        gpu_thread.start()
        while time.perf_counter() - began < args.seconds and not stop_threads.is_set():
            elapsed = time.perf_counter() - began
            vm = psutil.virtual_memory()
            row: dict[str, object] = {
                "elapsed_seconds": round(elapsed, 2),
                "cpu_percent": psutil.cpu_percent(interval=0.75),
                "ram_used_gb": round(vm.used / 1024**3, 3),
                "ram_available_gb": round(vm.available / 1024**3, 3),
            }
            if elapsed >= next_cpu_temp_check:
                last_cpu_temp = cpu_temperature_c()
                next_cpu_temp_check += 5.0
            row["cpu_temp_c"] = last_cpu_temp
            row.update(nvidia_metrics())
            samples.append(row)
            sensor_problem = gpu_sensor_problem(row)
            if sensor_problem:
                safety_abort_reason = sensor_problem
                stop_threads.set()
                break
            temp = row.get("gpu_temp_c")
            cpu_temp_text = f" {last_cpu_temp:.0f} C" if last_cpu_temp is not None else ""
            print(f"{elapsed:5.1f}s | CPU {row['cpu_percent']:5.1f}%{cpu_temp_text} | RAM used {row['ram_used_gb']:6.2f} GB"
                  + (f" | GPU {row.get('gpu_util_percent', 0):5.1f}% {temp:.0f} C" if temp is not None else " | GPU telemetry unavailable"))
            if temp is not None and float(temp) >= 87:
                safety_abort_reason = f"GPU temperature reached the 87 C hard limit ({float(temp):.0f} C)."
                stop_threads.set()
                break
            if last_cpu_temp is not None and last_cpu_temp >= 95:
                safety_abort_reason = f"CPU package temperature reached the 95 C hard limit ({last_cpu_temp:.0f} C)."
                stop_threads.set()
                break
            power = row.get("gpu_power_w")
            power_limit = row.get("gpu_power_limit_w")
            if power is not None and power_limit is not None and float(power) > max(float(power_limit) * 1.10, float(power_limit) + 20):
                safety_abort_reason = f"GPU power exceeded 110% of its reported limit ({float(power):.0f}/{float(power_limit):.0f} W)."
                stop_threads.set()
                break
            current_available = psutil.virtual_memory().available
            if current_available < 1024**3:
                safety_abort_reason = f"Available system RAM fell below the 1 GB hard reserve ({current_available/1024**3:.2f} GB)."
                stop_threads.set()
                break
            if elapsed >= next_whea_check:
                live_whea = whea_error_count(started_utc)
                next_whea_check += 10.0
                if whea_before is not None and live_whea is not None and live_whea > whea_before:
                    safety_abort_reason = "Windows logged a new WHEA hardware error during the burst."
                    stop_threads.set()
                    break
            remaining = 1.0 - (time.perf_counter() - began - elapsed)
            if remaining > 0:
                time.sleep(remaining)
    except KeyboardInterrupt:
        safety_abort_reason = "User interrupted the test before normal completion."
        stop_threads.set()
    except Exception as exc:
        safety_abort_reason = f"Test supervisor failed: {exc}"
        stop_threads.set()
    finally:
        load_finished_at = min(time.perf_counter(), deadline)
        stop_threads.set()
        stop_processes.set()
        cleanup_deadline = time.perf_counter() + 5.0
        for process in processes:
            if process.pid is None:
                continue
            process.join(timeout=max(0.0, cleanup_deadline - time.perf_counter()))
            if process.is_alive():
                forced_cpu_stops += 1
                process.terminate()
                process.join(timeout=0.2)
        if ram_thread.ident is not None:
            ram_thread.join(timeout=max(0.0, cleanup_deadline - time.perf_counter()))
        if gpu_thread.ident is not None:
            gpu_thread.join(timeout=max(0.0, cleanup_deadline - time.perf_counter()))
        if not any(p.is_alive() for p in processes if p.pid is not None) and not ram_thread.is_alive() and not gpu_thread.is_alive():
            watchdog_finished.set()
            if watchdog.pid is not None:
                watchdog.join(timeout=0.5)

    metric_keys = ["elapsed_seconds", "cpu_percent", "cpu_temp_c", "ram_used_gb", "ram_available_gb", "gpu_name",
                   "driver_version", "gpu_temp_c", "gpu_util_percent", "gpu_memory_used_mb",
                   "gpu_memory_total_mb", "gpu_power_w", "gpu_power_limit_w", "gpu_clock_mhz"]
    with (out_dir / "live_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(samples)

    ram = ram_results.get_nowait() if not ram_results.empty() else {"allocated_bytes": 0, "passes": 0,
                                                                    "errors": ["RAM worker returned no result."]}
    gpu = gpu_results.get_nowait() if not gpu_results.empty() else {"available": False, "iterations": 0,
                                                                    "errors": ["GPU worker returned no result."]}
    if not safety_abort_reason and gpu.get("errors"):
        safety_abort_reason = "CUDA tensor workload failed: " + str(gpu["errors"][0])
    if not safety_abort_reason and ram.get("errors"):
        safety_abort_reason = "RAM verification failed: " + str(ram["errors"][0])
    whea_after = whea_error_count(started_utc)
    whea = max(0, whea_after - whea_before) if whea_before is not None and whea_after is not None else None
    reasons: list[str] = []
    active_seconds = max(0.0, load_finished_at - began)
    if active_seconds < args.seconds - 0.25:
        reasons.append(f"Incomplete run: active window was {active_seconds:.2f}s of {args.seconds}s requested.")
    if forced_cpu_stops or any(p.pid is None or p.exitcode != 0 for p in processes):
        reasons.append("One or more CPU workers failed, did not start, or required forced termination.")
    if cpu_ops.value <= 0:
        reasons.append("No completed CPU hash workload was recorded.")
    if int(ram.get("passes", 0)) <= 0 or int(ram.get("allocated_bytes", 0)) < target_ram:
        reasons.append("RAM worker did not allocate the intended target and finish a verification pass.")
    if ram_thread.is_alive() or gpu_thread.is_alive():
        reasons.append("A workload did not stop within the cleanup deadline.")
    if safety_abort_reason:
        reasons.append(f"Safety abort: {safety_abort_reason} Test is aborted; hardware capability is not established.")
    if ram.get("errors"):
        reasons.extend(ram["errors"])
    if gpu.get("errors"):
        reasons.extend(gpu["errors"])
    if not gpu.get("available") or int(gpu.get("iterations", 0)) == 0:
        reasons.append("No successful CUDA tensor workload")
    if whea and whea > 0:
        reasons.append(f"Windows recorded {whea} recent WHEA hardware error(s)")

    max_temp = safe_number(samples, "gpu_temp_c", max)
    avg_gpu = safe_number(samples, "gpu_util_percent", statistics.mean)
    warnings: list[str] = []
    if ram_details["installed_ram_bytes"] is None:
        warnings.append("Installed physical RAM capacity was unavailable; scoring uses explicitly labelled OS-usable memory as a fallback.")
    if whea is None:
        warnings.append("Windows WHEA hardware-error evidence was unavailable; absence of hardware errors is unverified.")
    if max_temp is not None and max_temp >= 85:
        warnings.append("GPU ran at 85 C or hotter; inspect cooling before sustained AI production.")
    if avg_gpu is not None and avg_gpu < 50 and gpu.get("available"):
        warnings.append("Average GPU utilisation stayed below 50%; another bottleneck or telemetry issue may exist.")
    if safe_number(samples, "cpu_temp_c", max) is None:
        warnings.append("CPU package temperature was unavailable. Run LibreHardwareMonitor for CPU thermal safety telemetry.")

    status = "FAIL" if reasons else "PASS WITH WARNINGS" if warnings else "PASS"
    summary = {
        "status": status,
        "started_utc": started_utc,
        "requested_seconds": args.seconds,
        "actual_seconds": round(active_seconds, 2),
        "elapsed_including_cleanup_seconds": round(time.perf_counter() - began, 2),
        "system": {"os": platform.platform(), "python": sys.version,
                   "logical_cpu_count": os.cpu_count(), **ram_details},
        "cpu": {"workers": cpu_workers, "hash_operations": cpu_ops.value,
                "average_util_percent": safe_number(samples, "cpu_percent", statistics.mean),
                "peak_util_percent": safe_number(samples, "cpu_percent", max)},
        "ram": {**ram, "allocated_gb": round(int(ram.get("allocated_bytes", 0)) / 1024**3, 2)},
        "gpu": gpu,
        "telemetry": {"average_gpu_util_percent": avg_gpu, "peak_gpu_util_percent": safe_number(samples, "gpu_util_percent", max),
                      "max_gpu_temp_c": max_temp, "max_gpu_power_w": safe_number(samples, "gpu_power_w", max),
                      "max_cpu_temp_c": safe_number(samples, "cpu_temp_c", max),
                      "max_gpu_memory_used_mb": safe_number(samples, "gpu_memory_used_mb", max),
                      "gpu_total_vram_mb": safe_number(samples, "gpu_memory_total_mb", max)},
        "whea_errors_during_test": whea,
        "safety_abort": bool(safety_abort_reason),
        "safety_abort_reason": safety_abort_reason,
        "fail_reasons": reasons,
        "warnings": warnings,
        "scope_note": "A 60-second smoke test cannot certify long-term stability. Use MemTest86 and a longer production soak test before relying on new hardware.",
        "product": "SURVIVAL - 60 Seconds - Stress Test: Burst Mode",
        "local_revision": LOCAL_REVISION,
        "version": VERSION,
        "creator": CREATOR,
        "designed_by": "Jarvis AGI and Josh",
        "beta_disclaimer": "Use entirely at your own risk. Hardware stress testing may cause crashes, overheating, data loss or hardware damage. No liability is accepted to the fullest extent permitted by law, without excluding liability that cannot legally be excluded.",
    }
    summary["system"]["cpu_name"] = cpu_name
    summary["test_limits"] = {"cpu_temperature_available_at_start": initial_cpu_temp is not None,
                              "gpu_preflight": preflight_gpu,
                              "hard_workload_deadline_seconds": args.seconds,
                              "watchdog_cleanup_grace_seconds": 15,
                              "ram_target_bytes": target_ram,
                              "integrity_scope": "CPU load, sampled RAM bytes, and finite GPU scalar check; not exhaustive hardware validation."}
    summary["bottleneck_analysis"] = analyze_bottlenecks(summary, samples)
    summary["ai_dev_score"] = ai_dev_score(summary)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    score = summary["ai_dev_score"]
    score_text = f"{score['score']}/100 - {score['tier']}" if score['score'] is not None else score['tier']
    lines = ["SURVIVAL - 60 SECONDS - STRESS TEST: BURST MODE", f"Creator: {CREATOR}", "Designed by Jarvis AGI and Josh",
             LOCAL_REVISION, "=" * 52, f"RESULT: {status}", f"AI DEV READINESS SCORE: {score_text}",
             f"REFERENCE AI DEVELOPER LEVEL: {score['reference_score']}/100",
             f"PRIMARY BOTTLENECK: {summary['bottleneck_analysis']['primary_bottleneck']}",
             f"Duration: {summary['actual_seconds']} seconds", f"CPU average / peak: {summary['cpu']['average_util_percent']}% / {summary['cpu']['peak_util_percent']}%",
             f"Installed RAM: {ram_details['installed_ram_gb'] if ram_details['installed_ram_gb'] is not None else 'Unknown'} GiB; OS-usable RAM: {ram_details['usable_ram_gb']} GiB",
             f"RAM tested: {summary['ram']['allocated_gb']} GB; verification passes: {summary['ram'].get('passes', 0)}",
             f"CUDA device: {gpu.get('device', 'Unavailable')}", f"CUDA matrix iterations: {gpu.get('iterations', 0)}",
             f"Estimated tensor throughput: {gpu.get('tflops_estimate', 0)} TFLOPS", f"GPU average / peak: {avg_gpu}% / {summary['telemetry']['peak_gpu_util_percent']}%",
             f"GPU maximum temperature: {max_temp} C", f"GPU maximum power: {summary['telemetry']['max_gpu_power_w']} W",
             f"New WHEA errors during test: {whea if whea is not None else 'Unavailable'}"]
    if safety_abort_reason:
        lines += ["", "TEST ABORTED. HARDWARE CAPABILITY IS NOT ESTABLISHED.", f"Trigger: {safety_abort_reason}"]
    if reasons:
        lines += ["", "FAIL REASONS:"] + [f"- {item}" for item in reasons]
    if warnings:
        lines += ["", "WARNINGS:"] + [f"- {item}" for item in warnings]
    if summary["bottleneck_analysis"]["findings"]:
        lines += ["", "BOTTLENECK FINDINGS:"]
        for item in summary["bottleneck_analysis"]["findings"]:
            lines += [f"- {item['area']} [{item['severity']}]: {item['evidence']}",
                      f"  Action: {item['recommended_action']}"]
    lines += ["", summary["scope_note"], "", f"Detailed telemetry: {out_dir / 'live_metrics.csv'}"]
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    write_html_report(out_dir, summary, samples)
    print("\n" + "\n".join(lines))
    print(f"Visual report: {out_dir / 'SURVIVAL_Report.html'}")
    if status.startswith("PASS") and not safety_abort_reason and not args.no_browser:
        matrix_success_screen(int(score["score"]))
    if platform.system() == "Windows" and not args.no_browser:
        try:
            os.startfile(out_dir / "SURVIVAL_Report.html")  # type: ignore[attr-defined]
        except OSError:
            pass
    watchdog_finished.set()
    if watchdog.pid is not None:
        watchdog.join(timeout=0.5)
    return 0 if status.startswith("PASS") else 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
