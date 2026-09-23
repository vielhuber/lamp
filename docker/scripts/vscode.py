#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from uuid import uuid4


os.umask(0o077)
if sys.argv[1:2] == ['--lamp-host']:
    config, container = sys.argv[2:4]
    with tempfile.TemporaryDirectory(prefix='.code-', dir=config) as temporary:
        bridge = Path(temporary)
        environment = {**os.environ, 'LAMP_CODE_BRIDGE': '/etc/lamp-config/' + bridge.name}
        process = subprocess.Popen(sys.argv[4:], env=environment)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        extension_ready = False
        try:
            while process.poll() is None:
                for request in bridge.glob('*.request'):
                    result = {'status': 1}
                    try:
                        folder = json.loads(request.read_text())
                        if not isinstance(folder, str) or not folder.startswith('/'):
                            raise ValueError('Invalid container folder.')
                        executable = shutil.which('code')
                        if not executable:
                            raise ValueError('VS Code must be installed on the host with code in PATH.')
                        if not extension_ready:
                            extensions = subprocess.run([executable, '--remote', '', '--list-extensions'],
                                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                        text=True, timeout=30)
                            if extensions.returncode:
                                raise ValueError('Could not check the installed VS Code extensions.')
                            if 'ms-vscode-remote.remote-containers' not in extensions.stdout.splitlines():
                                installation = subprocess.run([executable, '--remote', '', '--install-extension',
                                                               'ms-vscode-remote.remote-containers'],
                                                              stdin=subprocess.DEVNULL, timeout=120)
                                if installation.returncode:
                                    raise ValueError('Could not install the VS Code Dev Containers extension.')
                            extension_ready = True
                        authority = json.dumps({'containerName': container}, separators=(',', ':')).encode().hex()
                        uri = 'vscode-remote://attached-container+' + authority + quote(folder, safe='/')
                        opened = subprocess.run([executable, '--folder-uri', uri], stdin=subprocess.DEVNULL, timeout=30)
                        result['status'] = opened.returncode
                    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                        result['error'] = str(error)
                    response = request.with_suffix('.response')
                    response.with_suffix('.tmp').write_text(json.dumps(result))
                    response.with_suffix('.tmp').replace(response)
                    request.unlink(missing_ok=True)
                time.sleep(0.1)
            status = process.returncode
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
    sys.exit(status if status >= 0 else 128 - status)

if len(sys.argv) > 2:
    sys.exit('Usage: code [folder]')
folder = Path(sys.argv[1] if len(sys.argv) == 2 else '.').resolve()
if not folder.is_dir():
    sys.exit('The folder does not exist: ' + str(folder))
bridge = Path(os.environ.get('LAMP_CODE_BRIDGE', '/nonexistent'))
if not bridge.is_dir():
    sys.exit('Open a new lamp ssh session on the host, then run code . again.')
request = bridge / (uuid4().hex + '.request')
response = request.with_suffix('.response')
request.with_suffix('.tmp').write_text(json.dumps(str(folder)))
request.with_suffix('.tmp').replace(request)
try:
    deadline = time.monotonic() + 180
    while not response.exists():
        if not bridge.is_dir() or time.monotonic() >= deadline:
            sys.exit('The host did not respond. Open a new lamp ssh session and retry.')
        time.sleep(0.1)
    result = json.loads(response.read_text())
    if result.get('error'):
        print(result['error'], file=sys.stderr)
    sys.exit(result['status'])
finally:
    request.unlink(missing_ok=True)
    response.unlink(missing_ok=True)
