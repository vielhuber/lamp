import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, path in [('CONFIGURATION', self.root), ('APACHE_SETTINGS', self.root / 'lamp.conf'), ('MAILNAME', self.root / 'mailname'),
                           ('SASL_PASSWORD', self.root / 'sasl_passwd')]:
            mocked = patch.object(control, name, path)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(control, 'run')
        self.run = mocked.start()
        self.addCleanup(mocked.stop)

    def test_optional_sections_are_validated(self):
        (self.root / 'config.yaml').write_text('domain: example.test\ngit:\n  name: Jane Doe\n  email: jane@example.test\n'
                                               'apache:\n  admin: admin@example.test\npostfix:\n  hostname: mail.example.test\n')
        settings = control.configuration()
        self.assertEqual({'name': 'Jane Doe', 'email': 'jane@example.test'}, settings['git'])
        (self.root / 'config.yaml').write_text('domain: example.test\ngit: null\n')
        self.assertIsNone(control.configuration()['git'])
        (self.root / 'config.yaml').write_text('domain: example.test\npostfix:\n  relayhost: "[smtp.example.test]:587"\n')
        self.assertEqual('[smtp.example.test]:587', control.configuration()['postfix']['relayhost'])
        for text in ['git: {nickname: x}', 'git: {name: ""}', 'git: {name: "a\\nb"}', 'git: [name]', 'apache: {admin: "a b"}',
                     'postfix: {hostname: "Mail.Example"}', 'postfix: {hostname: "a b.test"}', 'postfix: {relayhost: "smtp host"}',
                     'postfix: {relayhost: "smtp://x"}', 'postfix: {relayhost: "[a.test]:587", username: "u"}',
                     'postfix: {username: "u", password: "p"}', 'unknown: 1']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                (self.root / 'config.yaml').write_text('domain: example.test\n' + text + '\n')
                control.configuration()

    def test_settings_are_applied_and_fall_back_to_defaults(self):
        control.apply_settings({'domain': 'example.test', 'git': {'name': 'Jane Doe', 'email': 'jane@example.test'},
                                'apache': {'admin': 'admin@example.test'},
                                'postfix': {'hostname': 'mail.example.test', 'relayhost': '[smtp.example.test]:587', 'username': 'jane', 'password': 'p:w'}})
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual('[smtp.example.test]:587 jane:p:w\n', control.SASL_PASSWORD.read_text())
        self.assertEqual(0o600, control.SASL_PASSWORD.stat().st_mode & 0o777)
        self.assertIn(['postmap', str(control.SASL_PASSWORD)], commands)
        self.assertIn(['git', 'config', '--global', 'user.name', 'Jane Doe'], commands)
        self.assertIn(['git', 'config', '--global', 'user.email', 'jane@example.test'], commands)
        self.assertIn(['postconf', '-e', 'myhostname = mail.example.test', 'relayhost = [smtp.example.test]:587'], commands)
        self.assertEqual('Timeout 3000\nServerAdmin admin@example.test\nServerName localhost\n', control.APACHE_SETTINGS.read_text())
        self.assertEqual('mail.example.test\n', control.MAILNAME.read_text())
        self.run.reset_mock()
        with patch.object(control.subprocess, 'run') as unset:
            control.apply_settings({'domain': 'example.test'})
        self.assertEqual({'user.name', 'user.email'}, {call.args[0][-1] for call in unset.call_args_list if '--unset-all' in call.args[0]})
        self.assertFalse(any(call.args[0][:2] == ['git', 'config'] for call in self.run.call_args_list))
        self.assertEqual('Timeout 3000\nServerAdmin webmaster@localhost\nServerName localhost\n', control.APACHE_SETTINGS.read_text())
        self.assertEqual('lamp.localdomain\n', control.MAILNAME.read_text())
        self.assertIn(['postconf', '-e', 'myhostname = lamp.localdomain', 'relayhost = '], [call.args[0] for call in self.run.call_args_list])
        self.assertFalse(control.SASL_PASSWORD.exists())


if __name__ == '__main__':
    unittest.main()
