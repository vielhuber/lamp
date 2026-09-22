import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

import yaml


DOCKER = Path(__file__).resolve().parents[1]
RUNNER = '''import contextlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, os.environ['TEST_SCRIPTS'])
import control
root = Path(os.environ['TEST_ROOT'])
for name, directory in [('CONFIGURATION', 'data'), ('SETUP', 'config'), ('STATE', 'state'),
                        ('PROJECTS', 'projects'), ('SITES', 'sites'), ('ENABLED', 'enabled')]:
    setattr(control, name, root / directory)
control.HOSTS = root / 'hosts'
with (root / 'calls').open('a') as calls:
    calls.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'build':
    sys.exit(1 if sys.argv[2] == os.environ.get('TEST_FAIL_BUILD') else 0)
with contextlib.ExitStack() as stack:
    for name, value in [('run', ''), ('sync_visibility', None), ('connector', True),
                        ('vhost', None), ('reload_apache', None), ('ensure_vpn', None)]:
        stack.enter_context(patch.object(control, name, return_value=value))
    exists = Path.exists
    stack.enter_context(patch.object(Path, 'exists', lambda path: str(path) == '/.dockerenv' or exists(path)))
    control.main()
'''


class InitialEnvironmentsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ('data/build', 'data/syncdb', 'config', 'projects', 'state/environments',
                          'state/secrets', 'sites', 'enabled'):
            (self.root / directory).mkdir(parents=True)
        (self.root / 'state/secrets/database-password').write_text('test-password\n')
        (self.root / 'hosts').write_text('127.0.0.1 localhost\n')
        (self.root / 'config/setup.yaml').write_text('domain: example.test\n')
        (self.root / 'config/env.yaml').write_text('[]\n')
        (self.root / 'data/settings.yaml').write_text('vpn:\n  enabled: true\n  tunnels:\n    - name: nebro\n')
        (self.root / 'config/initial-environments').write_text(str(self.root / 'projects') + '\n')
        self.runner = self.root / 'controller.py'
        self.runner.write_text(RUNNER)
        self.script = self.root / 'initial-environments.sh'
        source = (DOCKER / 'scripts/initial-environments.sh').read_text()
        for before, after in [('/etc/lamp-config', 'config'), ('/etc/lamp', 'data'), ('/var/www', 'projects')]:
            source = source.replace(before, str(self.root / after))
        self.script.write_text(source.replace('/opt/lamp/control.py', str(self.runner)))
        self.environment = {**os.environ, 'TEST_ROOT': str(self.root), 'TEST_SCRIPTS': str(DOCKER / 'scripts'),
                            'PYTHONDONTWRITEBYTECODE': '1'}

    def project(self, name, build=None, webroots=(), php=None):
        project = self.root / 'projects' / name
        project.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        subprocess.run(['git', '-C', str(project), 'remote', 'add', 'origin', f'git@example.test:owner/{name}.git'], check=True)
        (project / 'local-work').write_text('keep')
        for webroot in webroots:
            (project / webroot).mkdir(parents=True)
        if php is not None:
            (project / '.phprc').write_text(php + '\n')
        if build is not None:
            (self.root / 'data/build' / f'example.test-owner-{name}.sh').write_text(build)
        return project

    def invoke(self):
        return subprocess.run(['bash', str(self.script)], env=self.environment, text=True, capture_output=True)

    def calls(self):
        file = self.root / 'calls'
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    def test_rules_preserve_checkouts_and_existing_environments_without_repeated_builds(self):
        self.project('My.Repo', 'db_engine postgres\nsyncdb "production"\n', ('_public', 'public', 'new', 'html/br-kk'), '8.3')
        self.project('nebro', 'echo build\n', ('public', 'new'))
        self.project('sqlite', "syncdb 'sqlite-production';\n", ('new', 'html/br-kk'))
        self.project('postgres', 'db_engine postgres\n', ('html/br-kk',))
        self.project('plain', '# postgres is only a comment\necho build\n')
        self.project('no-build')
        self.project('existing')
        (self.root / 'projects/unrelated').mkdir()
        existing = {'subdomain': 'custom', 'directory': 'existing', 'php': '8.2'}
        (self.root / 'config/env.yaml').write_text(yaml.safe_dump([existing]))
        for profile, engine, database in [('production', 'mysql', 'other_database'),
                                          ('sqlite-production', 'sqlite', '/data/storage/project.sqlite')]:
            (self.root / 'data/syncdb' / f'{profile}.json').write_text(json.dumps({'engine': engine, 'target': {'database': database}}))
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        entries = {entry['directory']: entry for entry in yaml.safe_load((self.root / 'config/env.yaml').read_text())}
        self.assertEqual(7, len(entries))
        self.assertEqual('custom', entries['existing']['subdomain'])
        self.assertEqual('8.2', entries['existing']['php'])
        expected_roots = {'My.Repo': '_public', 'nebro': 'public', 'sqlite': 'new', 'postgres': 'html/br-kk'}
        for name, entry in entries.items():
            self.assertEqual('keep', (self.root / 'projects' / name / 'local-work').read_text())
            if name == 'existing':
                continue
            self.assertEqual('main', entry['branch'])
            self.assertEqual('private', entry['visibility'])
            self.assertEqual('8.3' if name == 'My.Repo' else '8.5', entry['php'])
            self.assertEqual(expected_roots.get(name, '.'), entry['webroot'])
            self.assertEqual('nebro' if name == 'nebro' else None, entry['vpn'])
            for flag in ('build', 'aliases', 'proxy_port', 'proxy_exclude'):
                self.assertIsNone(entry.get(flag))
        self.assertEqual('my-repo', entries['My.Repo']['subdomain'])
        self.assertEqual(('mysql', 'other_database'), (entries['My.Repo']['db_engine'], entries['My.Repo']['db_name']))
        self.assertEqual(('sqlite', 'project'), (entries['sqlite']['db_engine'], entries['sqlite']['db_name']))
        self.assertEqual(('postgres', 'postgres'), (entries['postgres']['db_engine'], entries['postgres']['db_name']))
        self.assertIsNone(entries['plain']['db_name'])
        builds = [call[1] for call in self.calls() if call[0] == 'build']
        self.assertEqual(['my-repo', 'nebro', 'plain', 'postgres', 'sqlite'], builds)
        calls = self.calls()
        self.assertEqual(0, self.invoke().returncode)
        self.assertEqual(calls, self.calls())
        self.assertFalse((self.root / 'config/initial-environments').exists())
        self.assertFalse((self.root / 'config/initial-environments.jsonl').exists())

    def test_collision_leaves_environment_configuration_unchanged(self):
        self.project('My.Repo')
        self.project('my_repo')
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('duplicate subdomain', result.stderr)
        self.assertEqual('[]\n', (self.root / 'config/env.yaml').read_text())
        self.assertEqual([], self.calls())

    def test_conflicting_databases_abort_before_registration(self):
        self.project('conflict', 'syncdb first\nsyncdb second\n')
        for name in ('first', 'second'):
            (self.root / 'data/syncdb' / f'{name}.json').write_text(json.dumps({'engine': 'mysql', 'target': {'database': name}}))
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('different databases', result.stderr)
        self.assertEqual([], self.calls())

    def test_failed_build_resumes_without_rebuilding_completed_projects(self):
        self.project('first', 'echo build\n')
        self.project('second', 'echo build\n')
        self.environment['TEST_FAIL_BUILD'] = 'second'
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertTrue((self.root / 'config/initial-environments').exists())
        self.environment.pop('TEST_FAIL_BUILD')
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['first', 'second', 'second'], [call[1] for call in self.calls() if call[0] == 'build'])
        self.assertEqual(2, len(yaml.safe_load((self.root / 'config/env.yaml').read_text())))


class InitialSetupTest(unittest.TestCase):
    def test_interactive_setup_offers_folder_last_with_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / 'lamp'
            executable.write_text((DOCKER.parent / 'lamp').read_text())
            result = subprocess.run(['script', '-qec', 'bash ' + shlex.quote(str(executable)) + ' docker-setup', '/dev/null'],
                                    input='example.test\n\n\n', text=True, capture_output=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('Generate initial environments from folder', result.stdout)
            self.assertLess(result.stdout.index('Private git repository'), result.stdout.index('Generate initial environments'))
            self.assertEqual('/var/www\n', (root / '.config/initial-environments').read_text())
            self.assertEqual(0o600, (root / '.config/initial-environments').stat().st_mode & 0o777)

    def test_start_runs_pending_generation_after_container_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ('.config', 'docker', 'bin'):
                (root / directory).mkdir()
            (root / '.config/setup.yaml').write_text('domain: example.test\n')
            (root / '.config/initial-environments').write_text('/var/www\n')
            (root / 'docker/docker-compose.override.yml').write_text('services: {}\n')
            (root / 'lamp').write_text((DOCKER.parent / 'lamp').read_text())
            for command, body in [('docker', 'printf "%s\\n" "$*" >> "$TEST_CALLS"'), ('git', "printf '*\\n'")]:
                executable = root / 'bin' / command
                executable.write_text('#!/bin/bash\n' + body + '\n')
                executable.chmod(0o755)
            environment = {**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH'], 'TEST_CALLS': str(root / 'calls')}
            result = subprocess.run(['bash', str(root / 'lamp'), 'start'], env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            calls = (root / 'calls').read_text().splitlines()
            startup = next(index for index, call in enumerate(calls) if ' up -d ' in call)
            reconcile = next(index for index, call in enumerate(calls) if 'control.py reconcile' in call)
            generate = next(index for index, call in enumerate(calls) if 'initial-environments.sh' in call)
            self.assertLess(startup, reconcile)
            self.assertLess(reconcile, generate)


if __name__ == '__main__':
    unittest.main()
