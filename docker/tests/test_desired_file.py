import contextlib
import importlib.util
import io
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class DesiredFileTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name in ('CONFIGURATION', 'STATE', 'PROJECTS', 'SITES', 'ENABLED'):
            path = root / name
            path.mkdir()
            mocked = patch.object(control, name, path)
            mocked.start()
            self.addCleanup(mocked.stop)
        (root / 'hosts').write_text('127.0.0.1 localhost\n')
        for name, path in [('HOSTS', root / 'hosts')]:
            mocked = patch.object(control, name, path)
            mocked.start()
            self.addCleanup(mocked.stop)
        for name, arguments in [('run', {'return_value': ''}), ('sync_visibility', {}),
                                ('connector', {'return_value': True}),
                                ('vhost', {}), ('reload_apache', {}), ('ensure_vpn', {})]:
            mocked = patch.object(control, name, **arguments)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        (control.STATE / 'environments').mkdir()
        (control.STATE / 'secrets').mkdir()
        (control.STATE / 'secrets' / 'database-password').write_text('rootpw\n')
        (control.CONFIGURATION / 'config').mkdir()
        (control.CONFIGURATION / 'config' / 'settings.yaml').write_text('domain: example.test\n')
        self.settings = {'domain': 'example.test'}
        self.file = control.CONFIGURATION / 'config' / 'env.yaml'

    def invoke(self, *arguments):
        exists = Path.exists
        output = io.StringIO()
        with patch.object(control.sys, 'argv', ['control', *arguments]), \
             patch.object(Path, 'exists', lambda path: str(path) == '/.dockerenv' or exists(path)), \
             contextlib.redirect_stdout(output):
            control.main()
        return json.loads(output.getvalue())

    def reconcile(self):
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))

    def test_legacy_id_keyed_file_migrates_to_a_static_list(self):
        self.file.write_text('abcdef012345:\n  git: null\n  subdomain: one\n  php: "8.3"\n'
                             '123456abcdef:\n  git: git@example.test:owner/project.git\n  branch: main\n')
        entries, _ = control.read_desired()
        self.assertEqual([control.validate_specification({'subdomain': 'one', 'php': '8.3'})], entries)
        text = self.file.read_text()
        self.assertTrue(text.startswith('- '))
        self.assertNotIn('abcdef012345', text)
        self.assertEqual([{'git': None, 'branch': None, 'subdomain': 'one', 'directory': 'one', 'db_name': None, 'db_engine': None, 'webroot': None,
                           'php': '8.3', 'vpn': None, 'proxy_port': None, 'proxy_exclude': None, 'visibility': 'private'}], yaml.safe_load(text))

    def test_dynamic_entries_duplicates_and_empty_files_are_rejected(self):
        for text in ['- git: null\n  branch: main\n', '- subdomain: one\n- subdomain: one\n', '', 'one: two\n', '- [one]\n']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.file.write_text(text)
                control.read_desired()

    def test_add_records_static_entries_only_and_remove_deletes_them(self):
        control.write_desired([], None)
        (control.PROJECTS / 'site').mkdir()
        static = self.invoke('add', '--subdomain', 'site')
        dynamic = self.invoke('add')
        listed = control.validate_specification({'subdomain': 'site'})
        self.assertEqual([listed], control.read_desired()[0])
        self.assertEqual({static['id'], dynamic['id']}, {item['id'] for item in control.environments()})
        with patch.object(control, 'remove') as remove, patch.object(control, 'add') as add:
            self.reconcile()
        remove.assert_not_called()
        add.assert_not_called()
        self.invoke('remove', dynamic['id'])
        self.assertEqual([listed], control.read_desired()[0])
        self.invoke('remove', static['id'])
        self.assertEqual([], control.read_desired()[0])
        self.assertEqual([], control.environments())
        self.assertTrue((control.PROJECTS / 'site').is_dir())

    def test_entries_are_separated_by_blank_lines(self):
        control.write_desired([control.validate_specification({'subdomain': 'one'}), control.validate_specification({'subdomain': 'two'})], None)
        text = self.file.read_text()
        self.assertEqual(1, text.count('\n\n'))
        self.assertIn('  visibility: private\n\n- git: null\n', text)
        self.assertEqual(2, len(control.read_desired()[0]))
        self.assertEqual(text, self.file.read_text())
        control.write_desired([], control.read_desired()[1])
        self.assertEqual('[]\n', self.file.read_text())

    def test_services_are_reloaded_after_build_and_on_remove(self):
        control.write_desired([], None)
        identity = self.invoke('add', '--build', ':')['id']
        calls = [call.args[0] for call in self.run.call_args_list if call.args[0][0] == 'supervisorctl']
        self.assertEqual([['supervisorctl', 'reread'], ['supervisorctl', 'update'], ['supervisorctl', 'reread'], ['supervisorctl', 'update'],
                          ['supervisorctl', 'restart', 'php8.5-fpm']], calls)
        self.run.reset_mock()
        self.invoke('remove', identity)
        self.assertEqual([['supervisorctl', 'reread'], ['supervisorctl', 'update']],
                         [call.args[0] for call in self.run.call_args_list if call.args[0][0] == 'supervisorctl'])

    def test_reconcile_leaves_dynamic_environments_alone_even_when_their_build_changes(self):
        control.write_desired([], None)
        (control.CONFIGURATION / 'build').mkdir()
        dynamic = self.invoke('add', '--git', 'git@example.test:owner/project.git')
        (control.CONFIGURATION / 'build' / 'example.test-owner-project.sh').write_text('composer install\n')
        shutil.rmtree(control.PROJECTS / '_environments' / dynamic['id'])
        with patch.object(control, 'add', side_effect=AssertionError('dynamic environments must not be re-applied')), \
             patch.object(control, 'remove', side_effect=AssertionError('dynamic environments must not be removed')):
            self.reconcile()
        self.assertEqual('ready', control.load_environment(dynamic['id'])['status'])

    def test_reconcile_batches_reloads_restarts_and_access_checks(self):
        entries = []
        for label, php in (('one', '8.3'), ('two', '8.3'), ('three', '8.5')):
            (control.PROJECTS / label).mkdir()
            entries.append(control.validate_specification({'subdomain': label, 'php': php}))
        control.write_desired(entries, None)
        self.reconcile()
        self.assertEqual(3, len(control.environments()))
        self.assertEqual(1, self.reload_apache.call_count)
        self.assertEqual(1, self.connector.call_count)
        self.assertEqual(2, self.sync_visibility.call_count)
        self.assertEqual([['supervisorctl', 'restart', 'php8.3-fpm'], ['supervisorctl', 'restart', 'php8.5-fpm']],
                         [call.args[0] for call in self.run.call_args_list if call.args[0][:2] == ['supervisorctl', 'restart']])
        self.reload_apache.reset_mock()
        self.connector.reset_mock()
        self.reconcile()
        self.assertEqual(0, self.reload_apache.call_count)
        self.assertEqual(0, self.connector.call_count)

    def test_failed_entry_still_reloads_apache_once_at_the_end(self):
        (control.PROJECTS / 'fine').mkdir()
        entries = [control.validate_specification({'subdomain': 'fine'}), control.validate_specification({'subdomain': 'broken', 'build': 'exit 1'})]
        control.write_desired(entries, None)
        with self.assertRaisesRegex(RuntimeError, 'bash failed'):
            self.reconcile()
        self.assertEqual(1, self.reload_apache.call_count)
        self.assertEqual({'fine': 'ready', 'broken': 'failed'}, {item['subdomain']: item['status'] for item in control.environments()})

    def test_listed_entries_match_by_values_and_changes_replace_the_environment(self):
        (control.PROJECTS / 'site').mkdir()
        control.write_desired([control.validate_specification({'subdomain': 'site', 'php': '8.3'})], None)
        first = self.invoke('add', '--subdomain', 'site', '--php', '8.3')
        self.assertEqual(1, len(control.read_desired()[0]))
        self.assertEqual(first['id'], self.invoke('add', '--subdomain', 'site', '--php', '8.3')['id'])
        with patch.object(control, 'remove') as remove, patch.object(control, 'add') as add:
            self.reconcile()
        remove.assert_not_called()
        add.assert_not_called()
        control.write_desired([control.validate_specification({'subdomain': 'site', 'php': '8.4'})], control.read_desired()[1])
        self.reconcile()
        second = control.environments()
        self.assertEqual(1, len(second))
        self.assertNotEqual(first['id'], second[0]['id'])
        self.assertEqual('8.4', second[0]['php'])
        self.assertTrue((control.PROJECTS / 'site').is_dir())
        self.assertEqual(0, sum('DROP DATABASE' in call.kwargs.get('input', '') for call in self.run.call_args_list))


if __name__ == '__main__':
    unittest.main()
