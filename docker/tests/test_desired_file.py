import contextlib
import importlib.util
import io
import json
import os
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
        for name, arguments in [('run', {'return_value': ''}), ('sync_visibility', {}),
                                ('connector', {'return_value': True}), ('certificate', {}),
                                ('vhost', {}), ('reload_apache', {}), ('ensure_vpn', {})]:
            mocked = patch.object(control, name, **arguments)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        (control.STATE / 'environments').mkdir()
        (control.CONFIGURATION / 'config.yaml').write_text('domain: example.test\n')
        self.settings = {'domain': 'example.test'}
        self.file = control.CONFIGURATION / 'environments.yaml'

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
        self.assertEqual([{'git': None, 'branch': None, 'subdomain': 'one', 'webroot': None, 'php': '8.3', 'vpn': None,
                           'proxy_port': None, 'proxy_exclude': None, 'visibility': 'private'}], yaml.safe_load(text))

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
        self.assertEqual(2, sum('DROP DATABASE' in call.kwargs.get('input', '') for call in self.run.call_args_list))


if __name__ == '__main__':
    unittest.main()
