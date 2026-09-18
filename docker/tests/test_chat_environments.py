import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ChatEnvironmentsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ('configuration', 'state', 'projects'):
            (self.root / name).mkdir()
        (self.root / 'hosts').write_text('127.0.0.1 localhost\n')
        for name, folder in [('CONFIGURATION', 'configuration'), ('STATE', 'state'), ('PROJECTS', 'projects'), ('HOSTS', 'hosts')]:
            mocked = patch.object(control, name, self.root / folder)
            mocked.start()
            self.addCleanup(mocked.stop)
        (control.CONFIGURATION / 'config').mkdir()
        (control.CONFIGURATION / 'config' / 'settings.yaml').write_text('domain: example.invalid\n')
        self.identity = 'abcdef012345'
        self.project = control.PROJECTS / '_environments' / self.identity
        self.project.mkdir(parents=True)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.project / 'file.txt').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Base.')
        self.git('branch', 'feature/existing')
        self.git('remote', 'add', 'origin', str(self.project))
        self.desired = control.validate_specification({'git': 'git@example.invalid:owner/project.git', 'branch': 'main'})
        self.environment = {
            **self.desired, 'id': self.identity, 'path': str(self.project), 'url': 'https://abcdef012345.example.invalid',
            'status': 'ready', 'project_owned': True,
            'project_identity': [self.project.stat().st_dev, self.project.stat().st_ino],
            'applied': dict(self.desired), 'checkout': {'git': self.desired['git'], 'branch': 'main'}
        }
        (control.STATE / 'environments' / self.identity).mkdir(parents=True)
        control.save_environment(self.environment)
        control.write_desired([], None)

    def git(self, *arguments):
        return subprocess.run(['git', '-C', str(self.project), *arguments], capture_output=True, text=True, check=True).stdout.strip()

    def invoke(self, *arguments):
        exists = Path.exists
        output = io.StringIO()
        with patch('sys.argv', ['control', *arguments]), patch.object(Path, 'exists', lambda path: str(path) == '/.dockerenv' or exists(path)), contextlib.redirect_stdout(output):
            control.main()
        return json.loads(output.getvalue())

    def test_no_repository_is_valid_and_has_no_implicit_build(self):
        desired = control.validate_specification({'branch': 'main'})
        self.assertIsNone(desired['git'])
        self.assertIsNone(control.resolve_build(desired)[0])

    def test_failed_empty_clone_can_retry_with_a_corrected_branch(self):
        identity = 'fedcba654321'
        project = control.PROJECTS / '_environments' / identity
        project.mkdir()
        environment = {**self.environment, 'id': identity, 'path': str(project), 'status': 'failed',
                       'project_identity': [project.stat().st_dev, project.stat().st_ino]}
        with patch.object(control, 'run', side_effect=AssertionError('Empty directory has no Git state')):
            control.check_checkout(environment, {**self.desired, 'branch': 'feature/existing'})
        self.assertEqual([], list(project.iterdir()))

    def test_failed_nonempty_directory_is_not_treated_as_an_empty_clone(self):
        identity = 'fedcba654321'
        project = control.PROJECTS / '_environments' / identity
        project.mkdir()
        (project / 'keep.txt').write_text('keep')
        environment = {**self.environment, 'id': identity, 'path': str(project), 'status': 'failed',
                       'project_identity': [project.stat().st_dev, project.stat().st_ino]}
        with self.assertRaisesRegex(RuntimeError, 'git failed'):
            control.check_checkout(environment, {**self.desired, 'branch': 'feature/existing'})
        self.assertEqual('keep', (project / 'keep.txt').read_text())

    def test_idempotent_add_preserves_dirty_state_without_database_or_build(self):
        (self.project / 'file.txt').write_text('keep changes\n')
        with patch.object(control, 'database') as database, patch.object(control, 'add') as add:
            result = self.invoke('add', '--id', self.identity, '--git', self.desired['git'], '--branch', 'main')
            database.assert_not_called()
            add.assert_not_called()
        self.assertEqual(self.identity, result['id'])
        self.assertEqual('keep changes\n', (self.project / 'file.txt').read_text())
        with self.assertRaisesRegex(ValueError, 'different settings'):
            self.invoke('add', '--id', self.identity, '--git', self.desired['git'], '--branch', 'other')

    def test_dirty_switch_is_refused_without_parking_or_losing_any_files(self):
        for kind in ('unstaged', 'staged', 'untracked'):
            with self.subTest(kind=kind):
                path = self.project / ('new.txt' if kind == 'untracked' else 'file.txt')
                path.write_text('keep\n')
                if kind == 'staged':
                    self.git('add', '.')
                before = self.git('status', '--porcelain')
                with self.assertRaisesRegex(ValueError, 'Commit or discard'):
                    self.invoke('branch', self.identity, 'feature/existing')
                self.assertEqual(before, self.git('status', '--porcelain'))
                self.assertEqual('main', self.git('branch', '--show-current'))
                self.assertEqual('', self.git('for-each-ref', 'refs/harness'))
                self.git('reset', '--hard')
                self.git('clean', '-fd')

    def test_idempotent_add_rejects_an_out_of_band_branch_switch(self):
        self.git('switch', 'feature/existing')
        with self.assertRaisesRegex(ValueError, 'checkout branch differs'):
            self.invoke('add', '--id', self.identity, '--git', self.desired['git'], '--branch', 'main')
        self.assertEqual('feature/existing', self.git('branch', '--show-current'))

    def test_switch_and_rename_update_state_without_reimporting_databases(self):
        with patch.object(control, 'database') as database, patch.object(control, 'add') as add:
            result = self.invoke('branch', self.identity, 'feature/existing')
            self.assertEqual('feature/existing', result['branch'])
            self.invoke('branch', self.identity, 'feature/new', '--base', 'main')
            self.invoke('branch', self.identity, 'feature/renamed', '--operation', 'rename')
            database.assert_not_called()
            add.assert_not_called()
        self.assertEqual('feature/renamed', self.git('branch', '--show-current'))
        self.assertEqual([], control.read_desired()[0])
        self.assertEqual('feature/renamed', control.load_environment(self.identity)['checkout']['branch'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), control.load_environment(self.identity)['commit'])

    def test_failed_configuration_write_restores_previous_branch(self):
        project = control.PROJECTS / 'demo'
        self.project.rename(project)
        self.project = project
        listed = {**self.desired, 'subdomain': 'demo'}
        self.environment.update(path=str(project), subdomain='demo', applied=dict(listed),
                                project_identity=[project.stat().st_dev, project.stat().st_ino])
        control.save_environment(self.environment)
        control.write_desired([listed], control.read_desired()[1])
        with patch.object(control, 'write_desired', side_effect=ValueError('concurrent edit')):
            with self.assertRaisesRegex(ValueError, 'concurrent edit'):
                self.invoke('branch', self.identity, 'feature/existing')
        self.assertEqual('main', self.git('branch', '--show-current'))

    def test_ignored_files_and_foreign_index_locks_are_not_removed(self):
        self.git('switch', 'feature/existing')
        (self.project / 'local.env').write_text('tracked')
        self.git('add', '.')
        self.git('commit', '-m', 'Track environment.')
        self.git('switch', 'main')
        (self.project / '.git/info/exclude').write_text('local.env\n')
        (self.project / 'local.env').write_text('private configuration')
        with self.assertRaises(RuntimeError):
            self.invoke('branch', self.identity, 'feature/existing')
        self.assertEqual('private configuration', (self.project / 'local.env').read_text())
        lock = self.project / '.git/index.lock'
        lock.write_text('foreign process')
        with patch.object(control.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(ValueError, 'Git index is busy'):
                self.invoke('branch', self.identity, 'feature/existing')
        self.assertEqual('foreign process', lock.read_text())

    def test_exec_runs_the_script_in_the_environment_and_never_reaches_other_branches(self):
        self.addCleanup(os.chdir, os.getcwd())
        calls = []
        exists = Path.exists
        with patch('sys.argv', ['control', 'exec', self.identity, 'echo "$LAMP_ID"']), \
             patch.object(Path, 'exists', lambda path: str(path) == '/.dockerenv' or exists(path)), \
             patch.object(control.os, 'execvp', lambda *arguments: (calls.append(arguments), (_ for _ in ()).throw(SystemExit(0)))), \
             patch.object(control, 'remove', side_effect=AssertionError('exec must not remove')), \
             patch.object(control, 'write_desired', side_effect=AssertionError('exec must not write')), \
             patch.object(control, 'read_desired', side_effect=AssertionError('exec must not read the desired state')), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            control.main()
        self.assertEqual(1, len(calls))
        self.assertEqual(('bash', ['bash', '-c', 'set -e\necho "$LAMP_ID"']), calls[0])
        self.assertEqual(str(self.project), os.getcwd())

    def test_access_exports_only_required_headers_for_the_exact_environment(self):
        folder = control.CONFIGURATION / 'cloudflare'
        folder.mkdir()
        (folder / 'cloudflare-service-token.yaml').write_text(yaml.safe_dump({
            'CF-Access-Client-Id': 'test-id', 'CF-Access-Client-Secret': 'test-secret', 'unrelated': 'never-export'
        }))
        result = self.invoke('access', self.identity)
        self.assertEqual(self.environment['url'], result['origin'])
        self.assertEqual({'CF-Access-Client-Id': 'test-id', 'CF-Access-Client-Secret': 'test-secret'}, result['headers'])

    def test_read_only_commands_do_not_wait_for_provisioning(self):
        folder = control.CONFIGURATION / 'cloudflare'
        folder.mkdir()
        (folder / 'cloudflare-service-token.yaml').write_text(yaml.safe_dump({
            'CF-Access-Client-Id': 'test-id', 'CF-Access-Client-Secret': 'test-secret'
        }))
        with patch.object(control.fcntl, 'flock', side_effect=AssertionError('Provisioning lock requested')):
            self.assertEqual(self.identity, self.invoke('show', self.identity)['id'])
            self.assertEqual(self.identity, self.invoke('list')[0]['id'])
            self.assertEqual(self.environment['url'], self.invoke('access', self.identity)['origin'])

    def test_public_access_needs_no_service_token_and_reuse_keeps_visibility(self):
        self.environment['visibility'] = 'public'
        self.environment['applied']['visibility'] = 'public'
        control.save_environment(self.environment)
        self.assertEqual({}, self.invoke('access', self.identity)['headers'])
        self.assertEqual('public', self.invoke('add', '--id', self.identity)['visibility'])
        with self.assertRaisesRegex(ValueError, 'different settings'):
            self.invoke('add', '--id', self.identity, '--visibility', 'private')

    def test_idempotent_add_preserves_unspecified_custom_configuration(self):
        self.environment['applied']['webroot'] = 'public'
        control.save_environment(self.environment)
        with patch.object(control, 'add') as add:
            self.assertEqual(self.identity, self.invoke('add', '--id', self.identity,
                             '--git', self.desired['git'], '--branch', 'main')['id'])
            add.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'different settings'):
            self.invoke('add', '--id', self.identity, '--webroot', 'other')
        self.assertEqual('public', control.load_environment(self.identity)['applied']['webroot'])


if __name__ == '__main__':
    unittest.main()
