import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


LAMP = Path(__file__).resolve().parents[2] / 'lamp'


class SelfUpdateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.installation = self.root / 'installation with spaces'
        self.image = self.root / 'image'
        self.files = ('lamp', 'docker/docker-compose.yml', 'docker/docker-compose.data.yml', 'docker/scripts/vscode.py')
        for directory in (self.installation, self.image):
            for name in self.files:
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(LAMP.read_text() if name == 'lamp' else name + '\n')
            (directory / 'lamp').chmod(0o755)
        for name in ('.config/setup.yaml', '.config/env.yaml', '.data/settings.yaml', 'docker/docker-compose.override.yml'):
            path = self.installation / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('keep\n')
        binary = self.root / 'bin/docker'
        binary.parent.mkdir()
        binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ['TEST_ROOT'])
args = sys.argv[1:]
with (root / 'calls').open('a') as calls:
    calls.write(json.dumps(args) + '\\n')
if args[0] in ('info', 'image'):
    sys.exit(int(os.environ.get('TEST_DOCKER_UNAVAILABLE', '0')))
if args[0] == 'create':
    (root / 'container').touch()
    print('test-container')
elif args[0] == 'cp':
    source = args[1].split(':', 1)[1]
    if source == '/opt/lamp/vscode.py':
        source = '/app/docker/scripts/vscode.py'
    if source == os.environ.get('TEST_COPY_FAILURE'):
        sys.exit(1)
    shutil.copyfile(root / 'image' / source.removeprefix('/app/'), args[2])
elif args[0] == 'rm':
    (root / 'container').unlink()
else:
    sys.exit(99)
''')
        binary.chmod(0o755)
        self.environment = {**os.environ, 'TEST_ROOT': str(self.root),
                            'PATH': str(binary.parent) + ':' + os.environ['PATH']}
        self.environment.pop('LAMP_DEPLOYMENT_REFRESHED', None)

    def invoke(self, *arguments, **environment):
        return subprocess.run(['bash', str(self.installation / 'lamp'), *arguments],
                              env={**self.environment, **environment}, capture_output=True, text=True, timeout=10)

    def test_updates_all_files_and_reexecutes_with_original_arguments_once(self):
        candidate = re.sub(r'^DEPLOYMENT_SCRIPT_VERSION=\d+$', lambda match: 'DEPLOYMENT_SCRIPT_VERSION=' + str(int(match[0].split('=')[1]) + 1), LAMP.read_text(), flags=re.M)
        candidate = candidate.replace('command=${1:-help}', 'printf "%s\\n" "$@" > "$TEST_ROOT/arguments"\ncommand=${1:-help}')
        (self.image / 'lamp').write_text(candidate)
        (self.image / 'docker/docker-compose.yml').write_text('updated compose\n')
        result = self.invoke('unknown-command', 'argument with spaces', '$(do-not-execute)')
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual(['unknown-command', 'argument with spaces', '$(do-not-execute)'], (self.root / 'arguments').read_text().splitlines())
        for name in self.files:
            self.assertEqual((self.image / name).read_text(), (self.installation / name).read_text())
        self.assertEqual(0o755, (self.installation / 'lamp').stat().st_mode & 0o777)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertEqual(1, sum(call[0] == 'create' for call in calls))
        self.assertFalse(any(call[0] == 'pull' for call in calls))
        for name in ('.config/setup.yaml', '.config/env.yaml', '.data/settings.yaml', 'docker/docker-compose.override.yml'):
            self.assertEqual('keep\n', (self.installation / name).read_text())
        self.assertFalse((self.root / 'container').exists())
        self.assertEqual([], list(self.installation.glob('.lamp-update.*')))

    def test_updates_helpers_even_when_the_launcher_is_unchanged(self):
        (self.image / 'docker/scripts/vscode.py').write_text('updated helper\n')
        result = self.invoke('help')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('updated helper\n', (self.installation / 'docker/scripts/vscode.py').read_text())

    def test_identical_files_are_not_replaced(self):
        original = (self.installation / 'lamp').stat().st_ino
        result = self.invoke('help')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(original, (self.installation / 'lamp').stat().st_ino)

    def test_git_checkouts_and_worktrees_are_not_updated(self):
        marker = self.installation / '.git'
        for directory in (True, False):
            with self.subTest(directory=directory):
                marker.mkdir() if directory else marker.write_text('gitdir: elsewhere\n')
                result = self.invoke('help')
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse((self.root / 'calls').exists())
                marker.rmdir() if directory else marker.unlink()

    def test_missing_docker_and_failed_copy_leave_the_installation_usable(self):
        for environment in ({'TEST_DOCKER_UNAVAILABLE': '1'}, {'TEST_COPY_FAILURE': '/app/docker/docker-compose.data.yml'}):
            with self.subTest(environment=environment):
                (self.image / 'docker/docker-compose.yml').write_text('must not be installed\n')
                result = self.invoke('help', **environment)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual('docker/docker-compose.yml\n', (self.installation / 'docker/docker-compose.yml').read_text())
                self.assertFalse((self.root / 'container').exists())
                self.assertEqual([], list(self.installation.glob('.lamp-update.*')))

    def test_older_unversioned_and_invalid_scripts_are_not_installed(self):
        for candidate in ('#!/bin/bash\nDEPLOYMENT_SCRIPT_VERSION=0\n', '#!/bin/bash\nDEPLOYMENT_SCRIPT_VERSION=1\n',
                          '#!/bin/bash\necho legacy\n',
                          '#!/bin/bash\nDEPLOYMENT_SCRIPT_VERSION=2\nif\n'):
            with self.subTest(candidate=candidate):
                (self.image / 'lamp').write_text(candidate)
                result = self.invoke('help')
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(LAMP.read_text(), (self.installation / 'lamp').read_text())
                self.assertFalse((self.root / 'container').exists())


if __name__ == '__main__':
    unittest.main()
