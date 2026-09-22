import os
from pathlib import Path
import subprocess
import tempfile
import unittest


LAMP = Path(__file__).resolve().parents[2] / 'lamp'


class DockerResetTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ('bin', 'docker', '.config'):
            (self.root / directory).mkdir()
        (self.root / 'lamp').write_text(LAMP.read_text())
        (self.root / '.config/setup.yaml').write_text('domain: example.test\n')
        (self.root / 'docker/docker-compose.yml').write_text('name: lamp\nservices: {}\n')
        (self.root / 'docker/docker-compose.override.yml').write_text('services: {}\n')
        (self.root / 'local-work').write_text('keep')
        docker = self.root / 'bin/docker'
        docker.write_text('''#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> "$TEST_CALLS"
for argument in "$@"; do
    case "$argument" in
        ps) cat "$TEST_STATE"; exit 0 ;;
        down) [[ "$TEST_FAIL_RESET" = false ]] || exit 7; : > "$TEST_STATE"; exit 0 ;;
        build) exit 99 ;;
    esac
done
''')
        docker.chmod(0o755)
        self.calls = self.root / 'calls'
        self.state = self.root / 'state'
        self.environment = {**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                            'TEST_CALLS': str(self.calls), 'TEST_STATE': str(self.state), 'TEST_FAIL_RESET': 'false'}

    def invoke(self):
        return subprocess.run(['bash', str(self.root / 'lamp'), 'docker-reset'], env=self.environment,
                              capture_output=True, text=True, timeout=10)

    def test_reset_accepts_running_stopped_and_missing_containers_without_building(self):
        for state in ('running', 'restarting', 'paused', 'exited', 'created', 'dead', ''):
            with self.subTest(state=state):
                self.state.write_text(state)
                self.calls.write_text('')
                for _ in range(2):
                    result = self.invoke()
                    self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                calls = self.calls.read_text().splitlines()
                resets = [call for call in calls if ' down ' in call]
                self.assertEqual(2, len(resets))
                for call in resets:
                    self.assertIn('--volumes', call)
                    self.assertIn('--rmi all', call)
                    self.assertIn('--remove-orphans', call)
                    self.assertIn('docker-compose.override.yml', call)
                self.assertFalse(any(' build ' in call for call in calls))
                self.assertEqual('', self.state.read_text())
                self.assertEqual('keep', (self.root / 'local-work').read_text())
                self.assertFalse((self.root / '.data').exists())

    def test_reset_works_before_interactive_setup(self):
        (self.root / '.config/setup.yaml').unlink()
        (self.root / '.config').rmdir()
        (self.root / 'docker/docker-compose.override.yml').unlink()
        self.state.write_text('')
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn(' down ', self.calls.read_text())
        self.assertFalse((self.root / '.config').exists())

    def test_reset_failure_is_reported_without_building(self):
        self.state.write_text('running')
        self.environment['TEST_FAIL_RESET'] = 'true'
        result = self.invoke()
        self.assertEqual(7, result.returncode, result.stderr + result.stdout)
        self.assertFalse(any(' build ' in call for call in self.calls.read_text().splitlines()))
        self.assertEqual('keep', (self.root / 'local-work').read_text())

    def test_unavailable_docker_reports_wsl_integration_before_reset(self):
        (self.root / 'bin/docker').write_text('#!/bin/bash\nexit 1\n')
        self.environment['WSL_DISTRO_NAME'] = 'Ubuntu-test'
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('WSL Integration', result.stderr)
        self.assertIn('Ubuntu-test', result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertEqual('keep', (self.root / 'local-work').read_text())


if __name__ == '__main__':
    unittest.main()
