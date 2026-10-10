"""Bootstrap the bundled local service without requiring PowerShell scripts."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'
PYTHON = ROOT / 'integrations/wechat/runtime/venv/Scripts/python.exe'


def main():
    LOCAL.mkdir(exist_ok=True)
    if not PYTHON.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(PYTHON.parents[1])], check=True)
    check = subprocess.run([str(PYTHON), '-c', 'import cryptography, zstandard, Crypto, pilk_nogil, qrcode, png'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r',
                        str(ROOT / 'integrations/wechat/requirements.txt')], check=True)
    if not (ROOT / 'webui/node_modules/lucide').is_dir():
        subprocess.run(['npm.cmd', 'ci'], cwd=ROOT / 'webui', check=True)
    statefile = LOCAL / 'server.json'
    try:
        state = json.loads(statefile.read_text())
        with urlopen(state['url'] + '/api/sources', timeout=2) as response:
            if 'sources' in json.load(response):
                webbrowser.open(state['url'])
                return
    except (OSError, ValueError, KeyError):
        pass
    startupinfo = subprocess.STARTUPINFO() if os.name == 'nt' else None
    if startupinfo:
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    old_mtime = statefile.stat().st_mtime_ns if statefile.exists() else 0
    with (LOCAL / 'hub.out.log').open('w') as out, (LOCAL / 'hub.err.log').open('w') as err:
        process = subprocess.Popen([str(PYTHON), '-m', 'hub.server'], cwd=ROOT,
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=err,
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
