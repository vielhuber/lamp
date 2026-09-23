import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, path in [('CONFIGURATION', self.root), ('SETUP', self.root), ('APACHE_SETTINGS', self.root / 'lamp.conf'), ('MAILNAME', self.root / 'mailname'),
                           ('SASL_PASSWORD', self.root / 'sasl_passwd')]:
            mocked = patch.object(control, name, path)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(control, 'run')
        self.run = mocked.start()
        self.addCleanup(mocked.stop)
        (self.root / 'setup.yaml').write_text('domain: example.test\n')

    def test_optional_sections_are_validated(self):
        (self.root / 'settings.yaml').write_text('git:\n  name: Jane Doe\n  email: jane@example.test\n'
                                               'apache:\n  admin: admin@example.test\npostfix:\n  hostname: mail.example.test\n')
        settings = control.configuration()
        self.assertEqual({'name': 'Jane Doe', 'email': 'jane@example.test'}, settings['git'])
        (self.root / 'settings.yaml').write_text('git:\n  commit_key: sk-test\n  commit_url: https://ai.example.test/v1\n  commit_model: model-x\n  commit_effort: low\n')
        self.assertEqual('https://ai.example.test/v1', control.configuration()['git']['commit_url'])
        (self.root / 'settings.yaml').write_text('git: null\n')
        self.assertIsNone(control.configuration()['git'])
        (self.root / 'settings.yaml').write_text('postfix:\n  relayhost: "[smtp.example.test]:587"\n')
        self.assertEqual('[smtp.example.test]:587', control.configuration()['postfix']['relayhost'])
        (self.root / 'settings.yaml').write_text('cloudflare:\n  token: t0ken\n  email: jane@example.test\n')
        self.assertEqual({'token': 't0ken', 'email': 'jane@example.test'}, control.configuration()['cloudflare'])
        (self.root / 'settings.yaml').write_text('database:\n  password: root\n')
        self.assertEqual({'password': 'root'}, control.configuration()['database'])
        (self.root / 'settings.yaml').write_text('php:\n  xdebug: false\n')
        self.assertEqual({'xdebug': False}, control.configuration()['php'])
        for text in ['git: {nickname: x}', 'git: {name: ""}', 'git: {name: "a\\nb"}', 'git: [name]', 'git: {commit_key: "a b"}', 'git: {commit_token: x}', 'apache: {admin: "a b"}',
                     'postfix: {hostname: "Mail.Example"}', 'postfix: {hostname: "a b.test"}', 'postfix: {relayhost: "smtp host"}',
                     'postfix: {relayhost: "smtp://x"}', 'postfix: {relayhost: "[a.test]:587", username: "u"}',
                     'postfix: {username: "u", password: "p"}', 'cloudflare: {token: t}', 'cloudflare: {token: "a b", email: x@y}',
                     'cloudflare: {token: t, email: nomail}', 'database: {user: x}', 'database: {password: ""}', 'php: {xdebug: "no"}',
                     'php: {jit: true}', 'composer: {token: x}', 'composer: {github: "a b"}', 'unknown: 1', 'syncdb: {domains: []}', 'domain: example.test']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                (self.root / 'settings.yaml').write_text(text + '\n')
                control.configuration()

    def test_setup_holds_the_domain_and_the_data_repository(self):
        (self.root / 'setup.yaml').write_text('domain: example.test\ndata: git@example.test:owner/lamp-data.git\n')
        self.assertEqual({'domain': 'example.test'}, control.configuration())
        for text in ['data: git@example.test:owner/lamp-data.git', 'domain: example.test\nother: 1', 'domain: example.test\ndata: [x]', 'domain: Example.Test']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                (self.root / 'setup.yaml').write_text(text + '\n')
                control.configuration()

    def test_postfix_oauth_requires_complete_credentials_and_excludes_password(self):
        postfix = {'relayhost': '[smtp.office365.com]:587', 'username': 'noreply@example.test',
                   'sender': 'noreply@example.test', 'oauth_tenant_id': '11111111-1111-1111-1111-111111111111',
                   'oauth_client_id': '22222222-2222-2222-2222-222222222222', 'oauth_client_secret': 'test-secret'}
        (self.root / 'settings.yaml').write_text(control.yaml.safe_dump({'postfix': postfix}))
        self.assertEqual(postfix, control.configuration()['postfix'])
        invalid = [{**postfix, 'password': 'password'}, {**postfix, 'oauth_tenant_id': '../common'},
                   {**postfix, 'sender': 'invalid address'}, {**postfix, 'username': 'a:b@example.test'}]
        invalid.extend({key: value for key, value in postfix.items() if key != missing}
                       for missing in ('relayhost', 'username', 'oauth_tenant_id', 'oauth_client_id', 'oauth_client_secret'))
        for value in invalid:
            with self.subTest(keys=list(value)), self.assertRaises(ValueError):
                (self.root / 'settings.yaml').write_text(control.yaml.safe_dump({'postfix': value}))
                control.configuration()

    def test_postfix_oauth_refresh_is_atomic_and_keeps_the_previous_token_on_failure(self):
        postfix = {'relayhost': '[smtp.office365.com]:587', 'username': 'noreply@example.test',
                   'oauth_tenant_id': '11111111-1111-1111-1111-111111111111',
                   'oauth_client_id': '22222222-2222-2222-2222-222222222222', 'oauth_client_secret': 'test-secret'}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'access_token': 'test-token', 'expires_in': 3600}).encode()
        with patch.object(control, 'urlopen', return_value=response) as request, \
             patch.object(control.shutil, 'chown'), patch.object(control.time, 'time', return_value=1000):
            control.refresh_postfix({'postfix': postfix})
        token = self.root / 'lamp-oauth.json'
        self.assertEqual({'access_token': 'test-token', 'expiry': '4600', 'refresh_token': ''}, json.loads(token.read_text()))
        self.assertEqual(0o640, token.stat().st_mode & 0o777)
        self.assertIn(b'grant_type=client_credentials', request.call_args.args[0].data)
        self.assertIn(b'scope=https%3A%2F%2Foutlook.office365.com%2F.default', request.call_args.args[0].data)
        for payload in ({'error': 'invalid_client', 'error_description': 'private details'},
                        {'access_token': 'test-token', 'expires_in': 0}):
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            with patch.object(control, 'urlopen', return_value=response), self.assertRaises(RuntimeError) as raised:
                control.refresh_postfix({'postfix': postfix})
            self.assertNotIn('private details', str(raised.exception))
            self.assertEqual('test-token', json.loads(token.read_text())['access_token'])
        with patch.object(control, 'urlopen', side_effect=control.URLError('private details')), \
             self.assertRaises(RuntimeError) as raised:
            control.refresh_postfix({'postfix': postfix})
        self.assertNotIn('private details', str(raised.exception))
        self.assertEqual('test-token', json.loads(token.read_text())['access_token'])
        with patch.object(control, 'urlopen') as request:
            control.refresh_postfix({})
        request.assert_not_called()
        self.assertEqual(['lamp-oauth.json', 'setup.yaml'], sorted(path.name for path in self.root.iterdir()))

    def test_postfix_switches_between_oauth_password_and_direct_delivery(self):
        postfix = {'relayhost': '[smtp.office365.com]:587', 'username': 'noreply@example.test',
                   'sender': 'noreply@example.test', 'oauth_tenant_id': 'tenant'}
        with patch.object(control, 'refresh_postfix') as refresh:
            control.apply_postfix({'postfix': postfix})
            refresh.assert_called_once_with({'postfix': postfix})
        self.assertEqual(f'[smtp.office365.com]:587 noreply@example.test:{self.root}/lamp-oauth.json\n', control.SASL_PASSWORD.read_text())
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertTrue(any('smtp_sasl_mechanism_filter = xoauth2' in command for command in commands))
        self.assertTrue(any('smtp_tls_security_level = verify' in command for command in commands))
        self.assertTrue(any('sender_canonical_maps = static:noreply@example.test' in command for command in commands))
        (self.root / 'lamp-oauth.json').write_text('old token')
        self.run.reset_mock()
        control.apply_postfix({'postfix': {'relayhost': '[smtp.example.test]:587', 'username': 'user', 'password': 'password'}})
        self.assertFalse((self.root / 'lamp-oauth.json').exists())
        self.assertEqual('[smtp.example.test]:587 user:password\n', control.SASL_PASSWORD.read_text())
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertTrue(any('smtp_sasl_mechanism_filter = ' in command and 'sender_canonical_maps = ' in command for command in commands))
        control.apply_postfix({})
        self.assertFalse(control.SASL_PASSWORD.exists())

    def test_settings_are_applied_and_fall_back_to_defaults(self):
        control.apply_settings({'domain': 'example.test', 'git': {'name': 'Jane Doe', 'email': 'jane@example.test', 'commit_key': 'sk-test', 'commit_model': 'model-x'},
                                'apache': {'admin': 'admin@example.test'}, 'composer': {'github': 'ghp_test'},
                                'postfix': {'hostname': 'mail.example.test', 'relayhost': '[smtp.example.test]:587', 'username': 'jane', 'password': 'p:w'}})
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual('[smtp.example.test]:587 jane:p:w\n', control.SASL_PASSWORD.read_text())
        self.assertEqual(0o600, control.SASL_PASSWORD.stat().st_mode & 0o777)
        self.assertIn(['postmap', str(control.SASL_PASSWORD)], commands)
        self.assertIn(['git', 'config', '--global', 'user.name', 'Jane Doe'], commands)
        self.assertIn(['phpenmod', '-v', 'ALL', '-s', 'ALL', 'xdebug'], commands)
        self.assertIn(['composer', 'config', '--global', 'github-oauth.github.com', 'ghp_test'], commands)
        self.assertIn(['git', 'config', '--global', 'user.email', 'jane@example.test'], commands)
        self.assertIn(['git', 'config', '--global', 'commitmessage.key', 'sk-test'], commands)
        self.assertIn(['git', 'config', '--global', 'commitmessage.model', 'model-x'], commands)
        self.assertIn(['postconf', '-e', 'myhostname = mail.example.test', 'relayhost = [smtp.example.test]:587'], commands)
        self.assertEqual('Timeout 3000\nServerAdmin admin@example.test\nServerName localhost\n', control.APACHE_SETTINGS.read_text())
        self.assertEqual('mail.example.test\n', control.MAILNAME.read_text())
        self.run.reset_mock()
        with patch.object(control.subprocess, 'run') as unset:
            control.apply_settings({'domain': 'example.test'})
        self.assertEqual({'user.name', 'user.email', 'commitmessage.key', 'commitmessage.url', 'commitmessage.model', 'commitmessage.effort'},
                         {call.args[0][-1] for call in unset.call_args_list if '--unset-all' in call.args[0]})
        self.assertIn(['composer', 'config', '--global', '--unset', 'github-oauth.github.com'], [call.args[0] for call in unset.call_args_list])
        self.assertFalse(any(call.args[0][:2] == ['git', 'config'] for call in self.run.call_args_list))
        self.run.reset_mock()
        with patch.object(control.subprocess, 'run'):
            control.apply_settings({'domain': 'example.test', 'php': {'xdebug': False}})
        self.assertIn(['phpdismod', '-v', 'ALL', '-s', 'ALL', 'xdebug'], [call.args[0] for call in self.run.call_args_list])
        self.assertEqual('Timeout 3000\nServerAdmin webmaster@localhost\nServerName localhost\n', control.APACHE_SETTINGS.read_text())
        self.assertEqual('lamp.localdomain\n', control.MAILNAME.read_text())
        self.assertIn(['postconf', '-e', 'myhostname = lamp.localdomain', 'relayhost = '], [call.args[0] for call in self.run.call_args_list])
        self.assertFalse(control.SASL_PASSWORD.exists())

    def test_database_password_is_applied_to_both_servers_and_all_consumers(self):
        (self.root / 'secrets').mkdir()
        (self.root / 'secrets' / 'database-password').write_text('old\n')
        (self.root / 'environments').mkdir()
        with patch.object(control, 'STATE', self.root), patch.object(control, 'reload_apache') as reload, \
             patch.object(control, 'environments', return_value=[]), patch.object(control.Path, 'write_text') as write, \
             patch.object(control.Path, 'chmod'):
            self.assertFalse(control.apply_database_password({'domain': 'example.test'}))
            self.assertFalse(control.apply_database_password({'domain': 'example.test', 'database': {'password': 'old'}}))
            self.run.assert_not_called()
            self.assertTrue(control.apply_database_password({'domain': 'example.test', 'database': {'password': "ro'ot"}}))
        inputs = [call.kwargs.get('input', '') for call in self.run.call_args_list]
        self.assertIn("ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'ro\\'ot'; ALTER USER 'root'@'%' IDENTIFIED WITH mysql_native_password BY 'ro\\'ot';\n", inputs)
        self.assertIn("ALTER ROLE postgres PASSWORD 'ro''ot';\n", inputs)
        written = {str(call.args[0]) if call.args else None: call.args[-1] for call in write.call_args_list}
        self.assertEqual({"ro'ot\n", '[client]\nuser=root\npassword="ro\'ot"\n', "*:5432:*:postgres:ro'ot\n"}, set(written.values()))
        reload.assert_called_once()


if __name__ == '__main__':
    unittest.main()
