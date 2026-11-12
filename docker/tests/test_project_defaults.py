import importlib.util
from pathlib import Path
import tempfile
from textwrap import indent
import unittest
from unittest.mock import patch

import yaml


spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ProjectDefaultsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('CONFIGURATION', 'SETUP'):
            mocked = patch.object(control, name, self.root)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.file = self.root / 'settings.yaml'
        self.file.write_text('projects:\n  example.test/owner/project:\n'
                             '    webroot: html/site\n    aliases: [shop, blog]\n    php: "8.3"\n')

    def test_ssh_and_https_repositories_resolve_the_same_private_defaults(self):
        for remote in ('git@example.test:owner/project.git', 'https://example.test/owner/project.git',
                       'https://example.test/owner/project/'):
            with self.subTest(remote=remote):
                value = control.resolve_project_specification({'git': remote}, {'webroot': '.', 'php': '8.5'})
                self.assertEqual('html/site', value['webroot'])
                self.assertEqual(['shop', 'blog'], value['aliases'])
                self.assertEqual('8.3', value['php'])
                self.assertIsNone(value['subdomain'])
                self.assertIsNone(value['db_name'])
                self.assertEqual(['abcdef012345.example.test', 'abcdef012345-shop.example.test',
                                  'abcdef012345-blog.example.test'],
                                 control.environment_hostnames('abcdef012345', value, {'domain': 'example.test'}))

    def test_explicit_settings_override_project_defaults_and_can_clear_aliases(self):
        value = control.resolve_project_specification({'git': 'git@example.test:owner/project.git',
                                                       'webroot': 'public', 'aliases': [], 'php': '8.4'})
        self.assertEqual('public', value['webroot'])
        self.assertEqual([], value['aliases'])
        self.assertEqual('8.4', value['php'])
        control.write_desired([{**value, 'subdomain': 'site'}], None)
        entries, _ = control.read_desired()
        self.assertEqual([], entries[0]['aliases'])

    def test_project_branch_overrides_detection_but_not_an_explicit_work_branch(self):
        self.file.write_text('projects:\n  example.test/owner/project:\n    branch: master\n')
        for explicit, expected in (({}, 'master'), ({'branch': 'feature/task'}, 'feature/task'), ({'branch': None}, None)):
            with self.subTest(explicit=explicit):
                value = control.resolve_project_specification({'git': 'git@example.test:owner/project.git', **explicit}, {'branch': 'main'})
                self.assertEqual(expected, value['branch'])

    def test_env_yaml_inherits_missing_settings_and_preserves_explicit_values(self):
        (self.root / 'env.yaml').write_text('- git: git@example.test:owner/project.git\n  subdomain: site\n  webroot: custom\n')
        entries, _ = control.read_desired()
        self.assertEqual('custom', entries[0]['webroot'])
        self.assertEqual(['shop', 'blog'], entries[0]['aliases'])
        self.assertEqual('8.3', entries[0]['php'])

    def test_explicit_null_overrides_survive_saving_and_loading_env_yaml(self):
        (self.root / 'env.yaml').write_text('- git: git@example.test:owner/project.git\n  subdomain: site\n'
                                           '  php: null\n  aliases: null\n')
        for _ in range(2):
            entries, _ = control.read_desired()
            self.assertIsNone(entries[0]['php'])
            self.assertIsNone(entries[0]['aliases'])
        saved = yaml.safe_load((self.root / 'env.yaml').read_text())[0]
        self.assertIn('php', saved)
        self.assertIn('aliases', saved)

    def test_missing_file_and_unmatched_repositories_keep_automatic_settings(self):
        for present in (True, False):
            with self.subTest(present=present):
                if not present:
                    self.file.unlink()
                value = control.resolve_project_specification({'git': 'git@example.test:owner/other.git'}, {'webroot': '.'})
                self.assertEqual('.', value['webroot'])
                self.assertIsNone(value['aliases'])

    def test_invalid_project_files_fail_instead_of_silently_dropping_settings(self):
        for text in ('[]', 'example.test/owner/project: null',
                     'example.test/owner/project: {webroot: public, webroot: web}',
                     'example.test/owner/project: {}\nexample.test/owner/project: {}',
                     'example.test/owner/project: {db_name: shared}',
                     'example.test/owner/project.git: {}',
                     'example.test/owner/project: {webroot: ../outside}',
                     'example.test/owner/project: {aliases: [bad.name]}',
                     'example.test/owner/project: {branch: 42}',
                     'example.test/owner/project: {php: 8.3}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.file.write_text('projects:\n' + indent(text, '  '))
                control.resolve_project_specification({'git': 'git@example.test:owner/project.git'})
        self.file.write_text('projects:\n  example.test/owner/project: {branch: "bad..branch"}\n')
        with self.assertRaisesRegex(RuntimeError, 'git failed'):
            control.resolve_project_specification({'git': 'git@example.test:owner/project.git'})


if __name__ == '__main__':
    unittest.main()
