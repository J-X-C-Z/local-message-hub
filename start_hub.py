"""Bootstrap the bundled local service without requiring PowerShell scripts."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'
PYTHON = ROOT / 'integrations/wechat/runtime/venv/Scripts/python.exe'


def _running_hub_url(statefile):
    try:
        state = json.loads(statefile.read_text(encoding='utf-8'))
        url = state.get('url') if isinstance(state, dict) else None
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (parsed is None or parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.username or parsed.password or not parsed.port
                or parsed.path or parsed.query or parsed.fragment):
            return None
        try:
            with urlopen(url + '/api/sources', timeout=2) as response:
                result = json.load(response)
            return url if isinstance(result, dict) and isinstance(result.get('sources'), list) else None
        except HTTPError as error:
            with error:
                if error.code not in (401, 423):
                    return None
                result = json.loads(error.read(4096))
            return url if isinstance(result, dict) and result.get('code') == 'account_locked' else None
    except (OSError, ValueError, TypeError):
        return None


def _hub_environment():
    environment = os.environ.copy()
    ffmpeg = ROOT / 'node_modules/ffmpeg-static/ffmpeg.exe'
    if ffmpeg.is_file():
        environment['LMH_FFMPEG_PATH'] = str(ffmpeg)
    environment['QCE_BUNDLE_DIR'] = str(
        ROOT / '.runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64')
    return environment


def main():
    LOCAL.mkdir(exist_ok=True)
    statefile = LOCAL / 'server.json'
    running_url = _running_hub_url(statefile)
    if running_url:
        webbrowser.open(running_url)
        return
    if not PYTHON.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(PYTHON.parents[1])], check=True)
    check = subprocess.run([str(PYTHON), '-c', 'import cryptography, zstandard, Crypto, pilk_nogil, qrcode, png, ijson'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r',
                        str(ROOT / 'integrations/wechat/requirements.txt')], check=True)
    if not (ROOT / 'webui/node_modules/lucide').is_dir():
        subprocess.run(['npm.cmd', 'ci'], cwd=ROOT / 'webui', check=True)
    startupinfo = subprocess.STARTUPINFO() if os.name == 'nt' else None
    if startupinfo:
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    old_mtime = statefile.stat().st_mtime_ns if statefile.exists() else 0
    with (LOCAL / 'hub.out.log').open('w') as out, (LOCAL / 'hub.err.log').open('w') as err:
        process = subprocess.Popen([str(PYTHON), '-m', 'hub.server'], cwd=ROOT,
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   env=_hub_environment(),
                                   startupinfo=startupinfo, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError('Local service failed; see .local/hub.err.log')
        if statefile.exists() and statefile.stat().st_mtime_ns != old_mtime:
            state = json.loads(statefile.read_text())
            webbrowser.open(state['url'])
            return
        time.sleep(.25)
    raise RuntimeError('Local service startup timed out')


if __name__ == '__main__':
    main()
