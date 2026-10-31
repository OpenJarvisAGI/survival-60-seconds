"""Stub-only safety checks: never launch real stress workers or CUDA operations."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
source = Path(__file__).with_name('SURVIVAL_60_Seconds.py').resolve()
spec = importlib.util.spec_from_file_location('survival_checked', source)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
checks = []
valid = {'gpu_temp_c': 36., 'gpu_power_w': 7., 'gpu_power_limit_w': 215.}
assert m.gpu_sensor_problem(valid) is None
assert m.gpu_sensor_problem({})
assert m.gpu_sensor_problem({**valid, 'gpu_temp_c': 87.})
assert m.gpu_sensor_problem({**valid, 'gpu_power_w': 300.})
assert m.gpu_sensor_problem({**valid, 'gpu_temp_c': float('nan')})
checks.append('NVIDIA normal, missing, thermal-limit, overpower and NaN guards')

with patch.object(m.sys, 'argv', ['survival', '--seconds', '59']), contextlib.redirect_stdout(io.StringIO()):
    assert m.main() == 2
checks.append('Duration other than 60 seconds rejected before stress')

with patch.dict(sys.modules, {'torch': SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))}):
    try:
        m.gpu_preflight()
        raise AssertionError('Missing CUDA was accepted')
    except RuntimeError as exc:
        assert 'CUDA' in str(exc)
checks.append('CPU-only PyTorch rejected before stress')

class FakeEvent:
    def __init__(self): self.flag = False
    def set(self): self.flag = True
    def is_set(self): return self.flag

q = queue.Queue()
m.ram_worker(FakeEvent(), 256 * 1024**2, q, -1.)
assert q.get_nowait()['allocated_bytes'] == 0
checks.append('Expired RAM deadline allocates no memory')

class TimedEvent:
    def __init__(self, responses): self.responses = iter(responses)
    def wait(self, timeout): return next(self.responses)

stop = FakeEvent()
with patch.object(m.os, 'kill') as kill:
    m.deadline_watchdog(12345, -1., stop, TimedEvent([False, False]))
    assert stop.is_set()
    kill.assert_called_once_with(12345, m.signal.SIGTERM)
with patch.object(m.os, 'kill') as kill:
    m.deadline_watchdog(12345, -1., FakeEvent(), TimedEvent([True]))
    kill.assert_not_called()
checks.append('Watchdog requests stop and emergency kill; clean finish never kills')

with patch.object(m.platform, 'system', return_value='Windows'), patch.object(m.subprocess, 'run', return_value=SimpleNamespace(stdout='2\n')) as run:
    assert m.whea_error_count('2026-10-31T05:00:00+00:00') == 2
    assert '2026-10-31T05:00:00+00:00' in run.call_args.args[0][-1]
checks.append('WHEA query executes and uses the fixed test-start timestamp')

with patch.object(m.platform, 'system', return_value='Windows'), patch.object(m.subprocess, 'run', return_value=SimpleNamespace(stdout=str(32 * 1024**3))):
    ram_details = m.ram_capacity_details(int(31.85 * 1024**3))
assert ram_details['installed_ram_gb'] == 32.0
assert ram_details['usable_ram_gb'] == 31.85
assert ram_details['total_ram_gb'] == 32.0
ram_summary = {'status': 'PASS', 'safety_abort': False, 'requested_seconds': 60, 'actual_seconds': 60,
               'system': {**ram_details, 'logical_cpu_count': 16},
               'cpu': {'average_util_percent': 99}, 'ram': {'passes': 1, 'errors': []},
               'gpu': {'available': True, 'errors': [], 'tflops_estimate': 20},
               'telemetry': {'gpu_total_vram_mb': 8192, 'max_gpu_temp_c': 62, 'average_gpu_util_percent': 76},
               'whea_errors_during_test': 0, 'fail_reasons': []}
findings = m.analyze_bottlenecks(ram_summary, [])['findings']
assert not any(item['area'] == 'System RAM capacity' for item in findings)
assert m.ai_dev_score(ram_summary)['components']['RAM'] == 14.0
with patch.object(m.platform, 'system', return_value='Windows'), patch.object(m.subprocess, 'run', side_effect=OSError('CIM unavailable')):
    fallback = m.ram_capacity_details(int(31.85 * 1024**3))
assert fallback['installed_ram_gb'] is None and fallback['usable_ram_gb'] == 31.85
assert 'fallback' in fallback['ram_capacity_basis']
with patch.object(m.platform, 'system', return_value='Windows'), patch.object(m.subprocess, 'run', return_value=SimpleNamespace(stdout='0')):
    assert m.ram_capacity_details(32 * 1024**3)['installed_ram_gb'] is None
checks.append('32 GiB installed / 31.85 GiB usable remains 32 GiB for capacity finding and unchanged RAM score formula; unavailable or invalid CIM is labelled fallback')

class FakeProcess:
    def __init__(self, *args, **kwargs): self.pid = None; self.exitcode = None
    def start(self): self.pid = 12345; self.exitcode = 0
    def is_alive(self): return False
    def join(self, timeout=None): pass
    def terminate(self): self.exitcode = -1

class FakeThread:
    def __init__(self, target, args, **kwargs): self.target = target; self.args = args; self.ident = None
    def start(self):
        self.ident = 1
        if self.target is m.ram_worker:
            self.args[2].put({'allocated_bytes': self.args[1], 'passes': 1, 'errors': []})
        else:
            self.args[1].put({'available': True, 'iterations': 1, 'errors': []})
    def is_alive(self): return False
    def join(self, timeout=None): pass

def interrupt(**kwargs): raise KeyboardInterrupt()
fake_psutil = SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=16*1024**3, total=32*1024**3, used=16*1024**3), cpu_percent=interrupt)
with tempfile.TemporaryDirectory(dir=source.parent, prefix='guard-check-') as tmp:
    out = Path(tmp) / 'interrupted'
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, {'psutil': fake_psutil}))
        stack.enter_context(patch.object(m.sys, 'argv', ['survival', '--no-browser', '--output-dir', str(out)]))
        stack.enter_context(patch.object(m, 'gpu_preflight', return_value=valid))
        stack.enter_context(patch.object(m, 'cpu_temperature_c', return_value=None))
        stack.enter_context(patch.object(m, 'cpu_model_name', return_value='Stub CPU'))
        stack.enter_context(patch.object(m, 'ram_capacity_details', return_value=ram_details))
        stack.enter_context(patch.object(m, 'whea_error_count', return_value=0))
        stack.enter_context(patch.object(m.mp, 'Event', FakeEvent))
        stack.enter_context(patch.object(m.mp, 'Value', return_value=SimpleNamespace(value=1)))
        stack.enter_context(patch.object(m.mp, 'Process', FakeProcess))
        stack.enter_context(patch.object(m.threading, 'Thread', FakeThread))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        assert m.main() == 1
    report = json.loads((out / 'summary.json').read_text())
    assert report['status'] == 'FAIL'
    assert report['safety_abort']
    assert 'interrupted' in report['safety_abort_reason']
    assert any('Incomplete run' in reason for reason in report['fail_reasons'])
    assert report['ai_dev_score']['score'] is None
    assert 'UNSCORED' in (out / 'SURVIVAL_Report.html').read_text(encoding='utf-8')
    assert 'UNSCORED' in (out / 'summary.txt').read_text(encoding='utf-8')
checks.append('Fully stubbed interrupted run saves FAIL, incomplete duration and explicit user-abort reason')
checks.append('Failed run produces unscored JSON, text and HTML with no numeric score')
print(json.dumps({'status': 'PASS', 'stress_workers_executed': 0, 'checks': checks}, indent=2))
