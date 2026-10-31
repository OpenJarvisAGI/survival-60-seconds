# Third-party notices

SURVIVAL's own source and documentation are distributed under the [MIT License](LICENSE). Third-party software retains its own copyright, licence and notices; SURVIVAL's licence does not replace those terms.

## Dependencies installed separately

The source release does **not** bundle Python, PyTorch, psutil, uv, NVIDIA software or a virtual environment. `Setup_SURVIVAL.cmd` downloads the direct dependencies below into a local `.venv`:

| Dependency | Direct version pin | Official licence reference |
|---|---|---|
| psutil | `7.2.2` | [psutil LICENSE — BSD 3-Clause](https://github.com/giampaolo/psutil/blob/master/LICENSE) |
| PyTorch (`torch`) | `2.14.0+cu126` | [PyTorch LICENSE — BSD-style terms and contributor notices](https://github.com/pytorch/pytorch/blob/main/LICENSE) |
| uv - setup fallback only | `0.13.6` | [MIT](https://github.com/astral-sh/uv/blob/0.13.6/LICENSE-MIT) OR [Apache-2.0](https://github.com/astral-sh/uv/blob/0.13.6/LICENSE-APACHE), at your option |

uv is downloaded from [its official PyPI distribution](https://pypi.org/project/uv/0.13.6/) if the initial pip installation fails. It is used as an installation tool inside the local environment, not as a runtime requirement of the stress workload. Its own licence and included third-party notices remain applicable. Astral confirms the MIT-or-Apache-2.0 choice in its [licence policy](https://docs.astral.sh/uv/reference/policies/license/).

The links point to the projects' current source licence files, checked during release preparation on 31 October 2026. For the exact installed version, consult the licence files supplied with its package. PyTorch distributions and their dependencies may include additional third-party components and notices.

The setup uses [PyPI](https://pypi.org/project/psutil/) for psutil and the [official PyTorch CUDA 12.6 package index](https://download.pytorch.org/whl/cu126) for torch. Transitive dependencies are resolved by pip, or uv when the fallback is used, and are not fully locked. Their respective licence terms also apply.

Python and the NVIDIA driver are prerequisites installed separately by the user. Optional LibreHardwareMonitor or OpenHardwareMonitor software can provide CPU temperature readings; neither is bundled or installed by SURVIVAL. Consult each project's supplied licence before redistributing it.

## SURVIVAL licence template

The MIT template used for this release is published by [Choose a License](https://choosealicense.com/licenses/mit/). The project's copyright notice is **Copyright (c) 2026 SURVIVAL contributors**.
