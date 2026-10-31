"""Dependency/CUDA preflight only. Does not launch a stress workload."""
import platform
import sys


def main() -> int:
    if sys.version_info[:2] != (3, 14) or platform.architecture()[0] != '64bit':
        print('Python 3.14, 64-bit, is required for this pinned Windows setup.')
        return 2
    try:
        import psutil
        import torch
        if psutil.__version__ != '7.2.2' or torch.__version__ != '2.14.0+cu126':
            raise RuntimeError('Pinned dependency versions do not match. Run Setup_SURVIVAL.cmd.')
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable. Check the NVIDIA GPU and its driver.')
        props = torch.cuda.get_device_properties(0)
        print(f'Runtime ready: Python 3.14; psutil {psutil.__version__}; torch {torch.__version__}.')
        print(f'CUDA device: {props.name}. No stress workload was started.')
        return 0
    except Exception as exc:
        print(f'Runtime check failed: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
