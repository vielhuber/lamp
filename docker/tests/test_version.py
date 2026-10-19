import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class VersionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copyfile(ROOT / 'lamp', self.root / 'lamp')
        (self.root / 'lamp.sh').symlink_to('lamp')
        binary = self.root / 'bin/docker'
        binary.parent.mkdir()
        binary.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['TEST_CALLS']).open('a') as calls:
    calls.write(json.dumps(args) + '\\n')
if args[0] == 'compose' and 'ps' in args:
    print('active-container' if not os.environ.get('TEST_NO_CONTAINER') else '')
elif args[0] == 'inspect':
    template = args[args.index('--format') + 1]
    if 'org.opencontainers.image.source' in template:
        print(os.environ.get('TEST_IMAGE_VERSION', ''))
    elif '.Config.Image' in template:
        print('ghcr.io/vielhuber/lamp:latest')
    elif '.Image' in template:
        print('sha256:active-image')
    elif '.State.Status' in template:
        print('running')
    elif '.State.Health' in template:
        print('healthy')
    else:
        sys.exit(99)
else:
    sys.exit(99)
""")
        binary.chmod(0o755)
        self.calls = self.root / 'calls'
        self.environment = {**os.environ, 'PATH': str(binary.parent) + ':' + os.environ['PATH'],
                            'TEST_CALLS': str(self.calls)}
        self.environment.pop('LAMP_DEPLOYMENT_REFRESHED', None)

    def invoke(self, *arguments, **environment):
        return subprocess.run(['bash', str(self.root / 'lamp.sh'), 'version', *arguments],
                              env={**self.environment, **environment}, capture_output=True, text=True, timeout=10)

    def test_reports_the_active_image_release_without_refreshing_host_files(self):
        result = self.invoke(TEST_IMAGE_VERSION='2.2.0')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('Image version: 2.2.0\n', result.stdout)
        self.assertIn('ghcr.io/vielhuber/lamp:latest', result.stdout)
        self.assertIn('sha256:active-image', result.stdout)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertTrue(all(call[0] in ('compose', 'inspect') for call in calls))

    def test_old_images_do_not_report_the_ubuntu_version_as_lamp(self):
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('Image version: unknown', result.stdout)
        self.assertIn('sha256:active-image', result.stdout)

    def test_json_contains_image_version_and_preserves_the_status_contract(self):
        result = self.invoke('--json', TEST_IMAGE_VERSION='2.2.0')
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual('2.2.0', data['version'])
        self.assertEqual('ghcr.io/vielhuber/lamp:latest', data['image'])
        self.assertEqual('sha256:active-image', data['image_id'])
        self.assertEqual('running', data['state'])
        self.assertTrue(data['healthy'])
        self.assertEqual(1, data['api'])

    def test_missing_container_does_not_report_the_pulled_image_as_active(self):
        result = self.invoke(TEST_NO_CONTAINER='1')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('unavailable (no container)', result.stdout)
        self.assertNotIn('ghcr.io/vielhuber/lamp:latest', result.stdout)

    def test_shell_alias_is_shipped(self):
        self.assertTrue((ROOT / 'lamp.sh').is_symlink())
        self.assertEqual(ROOT / 'lamp', (ROOT / 'lamp.sh').resolve())


if __name__ == '__main__':
    unittest.main()
