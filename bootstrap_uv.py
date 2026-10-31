"""Install a pinned, verified uv wheel only in this project's virtual environment."""
import hashlib
import importlib.metadata
from pathlib import Path
import platform
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

UV_VERSION = '0.13.6'
UV_WHEEL = 'uv-0.13.6-py3-none-win_amd64.whl'
UV_URL = 'https://files.pythonhosted.org/packages/7c/9d/714523188aea3054644d8e69ae8089aac70325a3cee2d3d05509d4f23398/' + UV_WHEEL
UV_SHA256 = '7c9bc35ccb65932d8dc10a0669f9c805eaa3c9827ce18ecbecb1bd67944b2cdf'


def verified_context():
    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError('Certificate and hostname verification must remain enabled.')
    return context


def validate_source(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'files.pythonhosted.org' or parsed.username or parsed.password:
        raise RuntimeError('The uv bootstrap permits only the official PyPI file host over HTTPS.')


def main():
    expected_prefix = Path(__file__).resolve().parent / '.venv'
    if sys.prefix == sys.base_prefix or Path(sys.prefix).resolve() != expected_prefix:
        print('Bootstrap refused: run with this project\'s .venv\\Scripts\\python.exe.')
        return 2
    if sys.platform != 'win32' or sys.maxsize <= 2**32 or platform.machine().lower() not in ('amd64', 'x86_64'):
        print('This bootstrap is pinned for Windows x64.')
        return 2
    try:
        if importlib.metadata.version('uv') == UV_VERSION:
            print(f'Pinned uv {UV_VERSION} is already installed in the project environment.')
            return 0
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        validate_source(UV_URL)
        with tempfile.TemporaryDirectory(prefix='survival-uv-') as tmp:
            wheel_path = Path(tmp) / UV_WHEEL
            digest = hashlib.sha256()
            count = 0
            print(f'Downloading uv {UV_VERSION} from official PyPI with certificate and SHA-256 verification.', flush=True)
            with urllib.request.urlopen(UV_URL, context=verified_context(), timeout=30) as response:
                validate_source(response.geturl())
                with wheel_path.open('wb') as handle:
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        if count > 32 * 1024 * 1024:
                            raise RuntimeError('uv wheel exceeded the expected size limit.')
                        digest.update(chunk)
                        handle.write(chunk)
            if digest.hexdigest() != UV_SHA256:
                raise RuntimeError('uv wheel SHA-256 did not match the pinned official artifact.')
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps',
                            '--disable-pip-version-check', str(wheel_path)], check=True)
        if importlib.metadata.version('uv') != UV_VERSION:
            raise RuntimeError('Installed uv version does not match the pin.')
        return 0
    except Exception as exc:
        print(f'uv bootstrap failed: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
