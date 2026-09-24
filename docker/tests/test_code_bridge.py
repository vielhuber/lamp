import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from urllib.parse import unquote


BRIDGE = Path(__file__).resolve().parents[1] / 'scripts/vscode.py'


class CodeBridgeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'config').mkdir()
        (self.root / 'bin').mkdir()
        self.folder = self.root / 'folder with spaces # ü $(false)'
        self.folder.mkdir()
        executable = self.root / 'bin/code'
        executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with (Path(os.environ['TEST_ROOT']) / 'calls').open('a') as calls:
    calls.write(json.dumps(sys.argv[1:]) + '\\n')
if '--list-extensions' in sys.argv:
    print(os.environ.get('TEST_EXTENSIONS', ''))
    sys.exit(0)
if '--install-extension' in sys.argv:
    sys.exit(int(os.environ.get('TEST_INSTALL_STATUS', '0')))
sys.exit(int(os.environ.get('TEST_OPEN_STATUS', '0')))
''')
        executable.chmod(0o755)
        self.environment = {**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                            'TEST_ROOT': str(self.root), 'TEST_BRIDGE': str(BRIDGE), 'TEST_FOLDER': str(self.folder)}
        self.child = '''import os, subprocess, sys
from pathlib import Path
bridge = Path(os.environ['TEST_ROOT']) / 'config' / Path(os.environ['LAMP_CODE_BRIDGE']).name
assert bridge.stat().st_mode & 0o777 == 0o700
os.environ['LAMP_CODE_BRIDGE'] = str(bridge)
for _ in range(2):
    result = subprocess.run([sys.executable, os.environ['TEST_BRIDGE'], '.'], cwd=os.environ['TEST_FOLDER'])
    if result.returncode:
        sys.exit(result.returncode)
'''

    def invoke(self, **environment):
        result = subprocess.run(['python3', str(BRIDGE), '--lamp-host', str(self.root / 'config'), '/lamp-app-1',
                                 'python3', '-c', self.child], env={**self.environment, **environment},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual([], list((self.root / 'config').iterdir()))
        return result

    def test_opens_current_container_folder_and_installs_extension_once(self):
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertEqual(['--remote', '', '--list-extensions'], calls[0])
        self.assertEqual(['--remote', '', '--install-extension', 'ms-vscode-remote.remote-containers'], calls[1])
        self.assertEqual(4, len(calls))
        for arguments in calls[2:]:
            self.assertEqual('--folder-uri', arguments[0])
            authority, path = arguments[1].removeprefix('vscode-remote://attached-container+').split('/', 1)
            self.assertEqual({'containerName': '/lamp-app-1'}, json.loads(bytes.fromhex(authority)))
            self.assertEqual(str(self.folder), '/' + unquote(path))

    def test_failed_extension_install_does_not_open_folder(self):
        result = self.invoke(TEST_INSTALL_STATUS='7')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('Could not install', result.stderr)
        self.assertEqual(2, len((self.root / 'calls').read_text().splitlines()))

    def test_installed_extension_is_detected_silently_without_installation(self):
        result = self.invoke(TEST_EXTENSIONS='other.extension\nms-vscode-remote.remote-containers')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('', result.stdout)
        self.assertEqual('', result.stderr)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertEqual(['--remote', '', '--list-extensions'], calls[0])
        self.assertEqual(3, len(calls))
        self.assertTrue(all(arguments[0] == '--folder-uri' for arguments in calls[1:]))

    def test_host_open_failure_reaches_container_command(self):
        result = self.invoke(TEST_OPEN_STATUS='4')
        self.assertEqual(4, result.returncode)

    def test_client_without_session_explains_how_to_connect(self):
        environment = {**self.environment, 'LAMP_CODE_BRIDGE': str(self.root / 'missing')}
        result = subprocess.run(['python3', str(BRIDGE), '.'], env=environment, cwd=self.folder,
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('new lamp ssh session', result.stderr)

    def test_missing_folder_is_rejected_without_request(self):
        result = subprocess.run(['python3', str(BRIDGE), str(self.root / 'missing')],
                                env={**self.environment, 'LAMP_CODE_BRIDGE': str(self.root / 'config')},
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('folder does not exist', result.stderr)
        self.assertEqual([], list((self.root / 'config').iterdir()))

    def test_lamp_code_forwards_current_folder_without_interactive_shell(self):
        source = BRIDGE.parents[2]
        (self.root / 'lamp').write_text((source / 'lamp').read_text())
        (self.root / '.config').mkdir()
        (self.root / '.config/setup.yaml').write_text('domain: example.test\n')
        (self.root / 'docker/scripts').mkdir(parents=True)
        (self.root / 'docker/scripts/vscode.py').write_text(BRIDGE.read_text())
        (self.root / 'docker/docker-compose.override.yml').write_text('services: {}\n')
        docker = self.root / 'bin/docker'
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if 'ps' in sys.argv:
    print('container-id')
elif 'inspect' in sys.argv:
    print('/lamp-app-1')
elif 'exec' in sys.argv:
    Path(os.environ['TEST_ROOT'], 'docker-exec').write_text(json.dumps(sys.argv[1:]))
    assert os.environ['LAMP_CODE_BRIDGE'].startswith('/etc/lamp-config/.code-')
''')
        docker.chmod(0o755)
        result = subprocess.run(['bash', str(self.root / 'lamp'), 'code'], cwd=self.folder,
                                env=self.environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        arguments = json.loads((self.root / 'docker-exec').read_text())
        self.assertEqual(['exec', '-T', '-e', 'LAMP_CODE_BRIDGE', 'app', 'bash', '-c',
                          'cd -- "$1" && code .', 'bash', str(self.folder)], arguments[arguments.index('exec'):])
        self.assertEqual([], list((self.root / '.config').glob('.code-*')))

    def test_environment_shell_only_shows_status_when_git_marker_exists(self):
        launcher = (BRIDGE.parents[2] / 'lamp').read_text()
        line = next(line for line in launcher.splitlines() if 'setup.env && cd' in line)
        command = shlex.split(line)[-1].replace('source /var/lib/lamp/environments/$identity/setup.env', 'true')
        command = command.replace('\\$', '$')
        command = command.replace('exec bash -il', "printf 'interactive shell opened\\n'")
        for marker in ('missing', 'directory', 'file'):
            with self.subTest(marker=marker):
                project = self.root / marker
                project.mkdir()
                if marker != 'missing':
                    arguments = ['git', 'init', '--quiet']
                    if marker == 'file':
                        arguments += ['--separate-git-dir', str(self.root / 'git-storage')]
                    subprocess.run(arguments + [str(project)], check=True, capture_output=True)
                result = subprocess.run(['bash', '-c', command], text=True, capture_output=True,
                                        env={**os.environ, 'LAMP_PROJECT_DIR': str(project)}, timeout=5)
                self.assertEqual(0, result.returncode)
                self.assertEqual('', result.stderr)
                self.assertIn('interactive shell opened', result.stdout)
                self.assertEqual(marker != 'missing', 'On branch' in result.stdout)


if __name__ == '__main__':
    unittest.main()
