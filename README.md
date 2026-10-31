# SURVIVAL — 60 Seconds

**Version 1.3.1 · local repaired edition**

**Creator: Jarvis AGI**  
**Designed by Jarvis AGI and Josh**

SURVIVAL is a short, simultaneous CPU, sampled-RAM and NVIDIA CUDA load test. It records telemetry and generates local HTML, JSON, text and CSV reports. Its intended workload window is fixed at 60 seconds.

## Windows setup

1. Install **64-bit Python 3.14** with the Python launcher and a suitable NVIDIA driver for your CUDA-capable GPU.
2. Extract or clone this project into a writable folder.
3. Double-click **Setup_SURVIVAL.cmd**. It creates a local `.venv`, installs the pinned packages and checks CUDA availability. Setup does **not** start a stress test.
4. Save your work and close demanding applications. Double-click **Run_SURVIVAL_Burst_Mode.cmd**, read the notice and select **Y** to start.

The setup pins `psutil==7.2.2` from [PyPI](https://pypi.org/project/psutil/) and `torch==2.14.0+cu126` from the [official PyTorch CUDA 12.6 package index](https://download.pytorch.org/whl/cu126). The CUDA download is large. The setup does not install Python or change the system's drivers; all Python packages go into the project's `.venv`. Dependency installation needs internet access. Transitive dependencies are resolved by pip and are not fully locked.

If a pre-existing `.venv` uses another Python version, use a fresh project folder. Do not assume an arbitrary existing Python installation contains CUDA-enabled PyTorch.

Reports are written to a new timestamped folder under `outputs/SURVIVAL_Test_Records/`. The report opens after a normal command-line run. Nothing uploads automatically.

## Command-line options

From this project's folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe .\SURVIVAL_60_Seconds.py --seconds 60 --no-browser
```

| Option | Meaning |
|---|---|
| `--seconds 60` | Only 60 is accepted. |
| `--ram-percent 25` | Default: allocate 25% of available memory, subject to the cap and reserve. Valid range: 1–100. |
| `--ram-cap-gb 8` | Default cap: 8 GiB; positive finite values only. This is not a whole-RAM test. |
| `--output-dir <folder>` | Use this new run folder. It must not already exist. |
| `--no-browser` | Suppress browser opening and the success animation. |

Calling the Python program directly starts after preflight; it does not repeat the launcher's confirmation. `Ctrl+C` requests an abort, which produces an unscored failure rather than a successful result.

## Safeguards and limits

- CUDA availability and valid NVIDIA temperature/power readings are required before load begins. Loss of those readings during the run stops the test.
- GPU cutoff: **87°C**. CPU cutoff: **95°C when a CPU package sensor is available** through LibreHardwareMonitor or OpenHardwareMonitor WMI. Missing CPU temperature is explicitly warned about and the test may continue; this is not verified CPU thermal protection.
- The program preserves a 2 GiB initial RAM reserve and stops if available RAM drops below 1 GiB. Other applications can still change memory pressure between samples.
- Workers receive a 60-second deadline. An independent watchdog requests a stop at that deadline and terminates a hung parent after a further 15-second cleanup allowance. Sensor calls, GPU operations, process startup and shutdown mean elapsed wall time need not be exactly 60 seconds. A forced termination may leave no complete report.
- Interrupted, incomplete, failed-worker and safety-aborted runs are **UNSCORED**. A dependency or monitoring failure does not by itself prove defective hardware.
- CPU work computes SHA-512 hashes but does not validate them against reference outputs. RAM verification samples one byte per 4 KiB plus each block's final byte. GPU work checks a single output value for finiteness periodically; it is not full numerical verification or a full VRAM test.
- This is a brief load/smoke test. A pass does not certify long-term stability or the absence of hardware faults. Stress can expose instability, overheating or crashes and can lose unsaved work.
- The current NVIDIA telemetry query and CUDA workload target the first device. Multi-GPU device-order matching has not been validated; this edition is intended for a single NVIDIA GPU.

The **AI Dev Readiness Score** is an internal heuristic, not an industry benchmark: CPU 20, RAM 20, GPU/VRAM 50 and stability 10 points. The internal reference is 65/100. Its successful-run formula is retained. Missing WHEA evidence is reported as unverified; the inherited heuristic still awards the WHEA score term when evidence is unavailable, so do not interpret the number as a certification.

Installed RAM is obtained separately by summing Windows physical-memory module capacities. OS-usable RAM is retained as its own field. Where installed capacity is unavailable, the report labels the OS-usable fallback rather than claiming it is installed capacity. A machine with 32 GiB installed and approximately 31.85 GiB usable is not reported as having less than 32 GiB installed.

## Validation record

A retained **pre-packaging safety-repaired source** completed one 60.0-second run on **31 October 2026**:

| Observation | Recorded result |
|---|---|
| Result | PASS WITH WARNINGS |
| Internal heuristic score | 62/100 |
| CPU utilisation | 99.59% average; 100% peak |
| GPU utilisation | 76.39% average; 88% peak |
| Maximum GPU temperature | 62°C |
| Sampled RAM allocation | 3.83 GiB; 108 verification passes; no reported errors |
| New WHEA events | 0 reported |
| CPU temperature | Unavailable |

These observations describe that one historical run, not a performance promise. **Version 1.3.1's packaging, launcher and installed-versus-usable RAM changes have only received syntax and stub-based guard checks; no new stress run was performed for this package.** Raw machine reports, hostnames and user-specific paths are not included.

Run the no-load checks with the standard library only:

```powershell
py -3.14 .\test_survival_guards.py
```

These checks stub subprocesses, workers and CUDA; they cover sensor guards, invalid duration, missing CUDA, expired RAM deadlines, watchdog behavior, WHEA query anchoring, interrupted/unscored reporting, and the 32 GiB installed-RAM regression. They do not establish hardware stability or perform an end-to-end package installation.

## Repository contents and licensing

The repository contains source, launch/setup scripts, pinned direct dependencies and no-load checks. Generated reports, virtual environments and common credential files are ignored by Git. Review any files before deliberately publishing local diagnostics.

No license has been selected, and no license grant is included in this package.
