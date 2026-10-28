import json
import os
from pathlib import Path
import pty
import subprocess
import tempfile
import unittest


LAMP = Path(__file__).resolve().parents[2] / 'lamp'


class ImageUpdateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('bin', 'docker', '.config', '.git'):
            (self.root / name).mkdir()
        (self.root / 'lamp').write_text(LAMP.read_text())
        (self.root / 'lamp.sh').symlink_to('lamp')
        (self.root / '.config/setup.yaml').write_text('domain: example.test\n')
        (self.root / 'docker/docker-compose.yml').write_text('services: {}\n')
        (self.root / 'docker/docker-compose.override.yml').write_text('services: {}\n')
        self.calls = self.root / 'calls'
        self.binary = self.root / 'bin/docker'
        self.binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['TEST_ROOT'])
with (root / 'calls').open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[0] == 'info':
    sys.exit(0)
if args[0] == 'compose' and 'ps' in args:
    print(os.environ.get('TEST_STATE', 'exited'))
elif args[0] == 'pull':
    if os.environ.get('TEST_PROGRESS'):
        if not sys.stdout.isatty(): sys.exit(97)
        print('layer: Downloading 1MB/2MB\\r\\x1b[1Alayer: Downloading 2MB/2MB', flush=True)
    sys.exit(int(os.environ.get('TEST_PULL_EXIT', '0')))
elif args[0] == 'compose' and 'create' in args:
    result = int(os.environ.get('TEST_CREATE_EXIT', '0'))
    if not result: (root / 'new-image-protected').touch()
    sys.exit(result)
elif args[:2] == ['image', 'prune']:
    if not (root / 'new-image-protected').exists(): sys.exit(98)
    sys.exit(int(os.environ.get('TEST_PRUNE_EXIT', '0')))
else:
    sys.exit(99)
''')
        self.binary.chmod(0o755)
        self.environment = {**os.environ, 'TEST_ROOT': str(self.root),
                            'PATH': str(self.binary.parent) + ':' + os.environ['PATH']}

    def invoke(self, *arguments, **environment):
        return subprocess.run(['bash', str(self.root / 'lamp.sh'), 'update', *arguments],
                              env={**self.environment, **environment}, capture_output=True, text=True, timeout=10)

    def test_update_pulls_prepares_without_starting_and_prunes_only_lamp(self):
        for state in ('exited', 'created', 'dead', ''):
            with self.subTest(state=state):
                self.calls.write_text('')
                result = self.invoke(TEST_STATE=state)
                self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                changes = [call for call in calls if call[0] not in ('info',) and 'ps' not in call]
                self.assertEqual(['pull', 'ghcr.io/vielhuber/lamp:latest'], changes[0])
                self.assertEqual(['create', '--no-build', '--pull', 'never', 'app'], changes[1][-5:])
                self.assertEqual(['image', 'prune', '-af', '--filter',
                                  'label=org.opencontainers.image.source=https://github.com/vielhuber/lamp'], changes[2])
                self.assertEqual(3, len(changes))

    def test_update_rejects_active_containers_before_pulling(self):
        for state in ('running', 'restarting', 'paused'):
            with self.subTest(state=state):
                self.calls.write_text('')
                result = self.invoke(TEST_STATE=state)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('stop', result.stderr + result.stdout)
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                self.assertFalse(any(call[0] == 'pull' or 'create' in call or 'prune' in call for call in calls))

    def test_failed_pull_or_preparation_never_prunes(self):
        for variable, code in (('TEST_PULL_EXIT', 7), ('TEST_PULL_EXIT', 130), ('TEST_CREATE_EXIT', 8)):
            with self.subTest(variable=variable):
                self.calls.write_text('')
                result = self.invoke(**{variable: str(code)})
                self.assertEqual(code, result.returncode, result.stderr + result.stdout)
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                self.assertFalse(any('prune' in call for call in calls))
                if variable == 'TEST_PULL_EXIT':
                    self.assertFalse(any('create' in call for call in calls))

    def test_prune_failure_is_reported(self):
        self.assertEqual(9, self.invoke(TEST_PRUNE_EXIT='9').returncode)

    def test_pull_progress_survives_redirected_output_and_is_logged_without_ansi(self):
        result = self.invoke(TEST_PROGRESS='1')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        log = next((self.root / '.logs').glob('update-*.log')).read_text()
        for output in (result.stdout, log):
            self.assertIn('Downloading 1MB/2MB', output)
            self.assertIn('Downloading 2MB/2MB', output)
            self.assertNotIn('\x1b', output)

    def test_terminal_keeps_live_progress_while_log_strips_ansi(self):
        master, slave = pty.openpty()
        try:
            with subprocess.Popen(['bash', str(self.root / 'lamp'), 'update'],
                                  env={**self.environment, 'TEST_PROGRESS': '1'},
                                  stdin=subprocess.DEVNULL, stdout=slave, stderr=slave) as process:
                os.close(slave)
                slave = None
                output = bytearray()
                while True:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                self.assertEqual(0, process.wait(timeout=10), output.decode())
            self.assertIn(b'\x1b[1A', output)
            log = next((self.root / '.logs').glob('update-*.log')).read_text()
            self.assertIn('Downloading 2MB/2MB', log)
            self.assertNotIn('\x1b', log)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_extra_arguments_are_rejected(self):
        self.assertEqual(2, self.invoke('--background').returncode)
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
