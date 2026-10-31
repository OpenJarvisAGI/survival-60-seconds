# SURVIVAL 1.3.1

First public beta source release, available free of charge under the [MIT License](LICENSE).

**Creator: Jarvis AGI**  
**Designed by Jarvis AGI and Josh**

## Included

- A fixed 60-second CPU, sampled-RAM and NVIDIA CUDA workload with local HTML, JSON, text and CSV reports.
- Windows setup and launch scripts that create an isolated Python environment, pinned direct dependencies, and CUDA runtime checks before starting.
- GPU telemetry requirements, temperature and memory stops, worker deadlines, an independent watchdog, and unscored results for aborted or incomplete tests.
- Separate reporting of installed and OS-usable RAM, including a regression check for 32 GiB installed systems.
- Source, no-load guard checks, setup instructions, an example-report screenshot, and licensing notices. No standalone executable, Python installation, dependency packages, drivers or raw machine-specific reports are bundled.
- A pinned, hash-verified uv 0.13.6 setup fallback for environments where pip cannot complete secure downloads. Certificate checks remain enabled; no global packages or drivers are changed.
- Report header styling keeps the readiness-score label at a readable size.

## Validation

**Fresh-install verification passed on 31 October 2026.** The exact setup completed with exit code 0 in a new x64 Python 3.14.3 environment with system packages excluded. Normal pip timed out on this PC; the documented uv 0.13.6 fallback installed psutil 7.2.2 and torch 2.14.0+cu126 successfully. CUDA 12.6 detected the RTX 2070 SUPER; a tiny known-result 2 by 2 GPU multiplication passed. Dependency consistency, Python syntax and all ten no-load guard groups passed. No new 60-second stress test was run for this release. PyTorch may emit an optional NumPy-unavailable warning; NumPy was not required for these checks or this workload.

Python syntax checks and ten no-load guard groups passed during package preparation. These use stubs and do not run the stress workload.

A pre-packaging safety-repaired source completed a 60.0-second test on 31 October 2026 with **PASS WITH WARNINGS**, an internal score of **62/100**, CPU utilisation averaging **99.59%**, GPU utilisation averaging **76.39%**, and maximum GPU temperature **62°C**. No sampled RAM errors or new WHEA events were reported. CPU temperature was unavailable. This historical run preceded the final packaging, launcher and RAM-reporting changes; it is not a fresh full test of this release package.

## Known limits

Intended for Windows x64, x64 Python 3.14, and a single CUDA-capable NVIDIA GPU with a suitable driver. GPU/CPU utilisation depends on the system and workload; 100% on every component is not guaranteed.

CPU temperature protection depends on an available LibreHardwareMonitor/OpenHardwareMonitor CPU package sensor. The test warns and may continue when that sensor is unavailable. CPU hashes are not compared to reference answers; RAM verification samples bytes; GPU verification periodically checks one output value. These are limited smoke checks, not complete component integrity tests.

The readiness score is a project heuristic. Missing WHEA evidence is marked unverified but still receives its inherited score term. A pass or score does not certify hardware health. See the [README](README.md) for the complete safeguards, timing limits and usage instructions.
