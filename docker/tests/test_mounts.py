from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


LAMP = Path(__file__).resolve().parents[2] / 'lamp'


class MountsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / '.config').mkdir()
        (self.root / 'docker').mkdir()
        self.override = self.root / 'docker/docker-compose.override.yml'
        source = LAMP.read_text()
        self.functions = source[source.index('configured_mounts() {'):source.index('\n# with a data repository /etc/lamp is a clone')]

    def generate(self, setup):
        (self.root / '.config/setup.yaml').write_text(setup)
        return subprocess.run(['bash', '-c', 'set -euo pipefail\n' + self.functions + '\nwrite_mount_override\n'],
                              env={'PATH': '/usr/bin:/bin', 'directory': str(self.root)},
                              capture_output=True, text=True, timeout=10)

    def test_mounts_become_the_compose_override(self):
        result = self.generate('domain: example.test\n\ndata: git@example.test:owner/data.git\n\nmounts:\n'
                               '    - /var/www:/var/www\n'
                               "    - /mnt/c/Users/Jane/OneDrive - it's.test/FOTOS:/mnt/c/Users/Jane/OneDrive - it's.test/FOTOS:ro  # photos\n")
        self.assertEqual(0, result.returncode, result.stderr)
        volumes = yaml.safe_load(self.override.read_text())['services']['app']['volumes']
        photos = "/mnt/c/Users/Jane/OneDrive - it's.test/FOTOS"
        self.assertEqual([
            {'type': 'bind', 'source': '/var/www', 'target': '/var/www', 'bind': {'create_host_path': False}},
            {'type': 'bind', 'source': photos, 'target': photos, 'read_only': True, 'bind': {'create_host_path': False}}
        ], volumes)

    def test_without_mounts_the_existing_override_stays(self):
        self.override.write_text('manual\n')
        result = self.generate('domain: example.test\n')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('manual\n', self.override.read_text())

    def test_invalid_mount_is_rejected_without_touching_the_override(self):
        self.override.write_text('manual\n')
        result = self.generate('domain: example.test\nmounts:\n    - var/www:/var/www\n')
        self.assertEqual(2, result.returncode)
        self.assertIn('Invalid mount', result.stderr)
        self.assertEqual('manual\n', self.override.read_text())


if __name__ == '__main__':
    unittest.main()
