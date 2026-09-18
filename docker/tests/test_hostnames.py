import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class HostnamesTest(unittest.TestCase):
    def test_explicit_subdomains_share_the_first_project_path(self):
        labels = ['site', 'shop', 'blog', 'events', 'press', 'jobs']
        value = control.validate_specification({'subdomain': labels})
        settings = {'domain': 'example.test'}
        identity = '0123456789ab'
        self.assertEqual([label + '.example.test' for label in labels],
                         control.environment_hostnames(identity, value, settings))
        self.assertEqual('https://site.example.test', control.environment_url(identity, value, settings))
        with tempfile.TemporaryDirectory() as directory, patch.object(control, 'PROJECTS', Path(directory)):
            self.assertEqual(Path(directory) / 'site', control.project_path(identity, value))
            self.assertEqual(control.project_path(identity, {'subdomain': 'site'}), control.project_path(identity, value))

    def test_directory_changes_the_project_path_but_not_the_hostname(self):
        value = control.validate_specification({'subdomain': 'ai', 'directory': 'aistats'})
        settings = {'domain': 'example.test'}
        self.assertEqual('https://ai.example.test', control.environment_url('abcdef012345', value, settings))
        with tempfile.TemporaryDirectory() as directory, patch.object(control, 'PROJECTS', Path(directory)):
            self.assertEqual(Path(directory) / 'aistats', control.project_path('abcdef012345', value))
        self.assertEqual('ai', control.ordered_settings(control.validate_specification({'subdomain': 'ai'}))['directory'])
        self.assertEqual('aistats', control.ordered_settings(value)['directory'])
        nested = control.validate_specification({'subdomain': 'tour', 'directory': 'tourconcept/new'})
        with tempfile.TemporaryDirectory() as directory, patch.object(control, 'PROJECTS', Path(directory)):
            self.assertEqual(Path(directory) / 'tourconcept' / 'new', control.project_path('abcdef012345', nested))
        for settings in [{'directory': 'aistats'}, {'subdomain': 'ai', 'directory': 'Ai Stats'}, {'subdomain': 'ai', 'directory': '../x'},
                         {'subdomain': 'ai', 'directory': ''}, {'subdomain': 'ai', 'directory': 42}, {'subdomain': 'ai', 'directory': 'a/../b'}, {'subdomain': 'ai', 'directory': '/abs'}]:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                control.validate_specification(settings)

    def test_invalid_subdomain_lists_are_rejected(self):
        for labels in [[], [''], ['valid', None], ['valid', 42], ['valid', []], ['valid', 'UPPER'],
                       ['valid', 'a.b'], ['valid', '../path'], ['valid', 'line\nHost'],
                       ['same', 'same'], ['valid', 'a' * 64]]:
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                control.validate_specification({'subdomain': labels})

    def test_subdomain_list_roundtrips_through_yaml(self):
        identity = 'abcdef012345'
        value = control.validate_specification({'subdomain': ['one', 'two']})
        with tempfile.TemporaryDirectory() as directory, patch.object(control, 'CONFIGURATION', Path(directory)):
            (Path(directory) / 'config').mkdir()
            control.write_desired([value], None)
            entries, original = control.read_desired()
            self.assertEqual(['one', 'two'], entries[0]['subdomain'])
            self.assertEqual(original, (Path(directory) / 'config' / 'env.yaml').read_bytes())

    def test_explicit_subdomains_collide_with_other_hosts_and_aliases(self):
        first = control.validate_specification({'subdomain': ['demo', 'other']})
        for second in [{'subdomain': 'other'}, {'subdomain': ['third', 'other']},
                       {'subdomain': 'base', 'aliases': ['part']}]:
            primary = dict(first, subdomain=['demo', 'base-part']) if second.get('aliases') else first
            with self.subTest(second=second), self.assertRaisesRegex(ValueError, 'same domain'):
                control.validate_domains({'abcdef012345': primary, '123456abcdef': control.validate_specification(second)},
                                         {'domain': 'example.test'})
        with self.assertRaisesRegex(ValueError, 'same domain'):
            control.validate_domains({'abcdef012345': control.validate_specification({
                'subdomain': ['demo', 'demo-part'], 'aliases': ['part']})}, {'domain': 'example.test'})

    def test_array_aliases_use_only_the_primary_label(self):
        value = control.validate_specification({'subdomain': ['one', 'two'], 'aliases': ['extra']})
        self.assertEqual(['one.example.test', 'two.example.test', 'one-extra.example.test'],
                         control.environment_hostnames('abcdef012345', value, {'domain': 'example.test'}))

    def test_explicit_hosts_reach_the_vhost_and_show(self):
        identity = '0123456789ab'
        value = control.validate_specification({'subdomain': ['site', 'shop', 'blog', 'events', 'press', 'jobs']})
        settings = {'domain': 'example.test'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {**value, 'id': identity, 'hostname': control.environment_hostname(identity, value, settings),
                           'hostnames': control.environment_hostnames(identity, value, settings),
                           'url': control.environment_url(identity, value, settings),
                           'document_root': str(root / 'html/site'), 'php': '8.4', 'path': str(root / 'html'),
                           'engine': 'mysql', 'db_name': 'site', 'db_engine': 'mysql', 'password': 'a' * 48}
            (root / 'secrets').mkdir()
            (root / 'secrets' / 'database-password').write_text('rootpw\n')
            with patch.object(control, 'SITES', root), patch.object(control, 'ENABLED', root), patch.object(control, 'STATE', root):
                control.vhost(environment)
            vhost = (root / ('lamp-' + identity + '.conf')).read_text()
            self.assertIn('    SetEnv DB_DATABASE "site"\n', vhost)
            self.assertIn('    SetEnv DB_USERNAME "root"\n', vhost)
            self.assertIn('    SetEnv DB_PASSWORD "rootpw"\n', vhost)
            self.assertNotIn('lamp_' + identity, vhost)
            self.assertIn('    SetEnv APP_URL "https://site.example.test"\n', vhost)
            self.assertIn('    SetEnv LAMP_DATA_DIR "' + str(root / 'environments' / identity / 'data') + '"\n', vhost)
            self.assertEqual(1, vhost.count('ServerAlias ' + ' '.join(environment['hostnames'][1:])))
            self.assertEqual(1, vhost.count('DocumentRoot "' + environment['document_root'] + '"'))
            self.assertEqual(1, vhost.count('<VirtualHost *:443>'))
            self.assertIn('SSLCertificateFile "/etc/letsencrypt/live/example.test/fullchain.pem"', vhost)
            self.assertNotIn('8081', vhost)
            self.assertEqual(['https://' + host for host in environment['hostnames']], control.show(environment)['urls'])
            self.assertEqual(value['subdomain'], control.show(environment)['subdomain'])

    def test_generated_hosts_share_one_project(self):
        value = control.validate_specification({'aliases': ['shop', 'blog', 'events', 'press-room', 'jobs']})
        hosts = control.environment_hostnames('abcdef012345', value, {'domain': 'example.test'})
        self.assertEqual(['abcdef012345.example.test', 'abcdef012345-shop.example.test',
                          'abcdef012345-blog.example.test', 'abcdef012345-events.example.test',
                          'abcdef012345-press-room.example.test', 'abcdef012345-jobs.example.test'], hosts)
        self.assertIsNone(value['subdomain'])
        self.assertNotIn('aliases', control.ordered_settings(control.validate_specification({})))

    def test_invalid_aliases_are_rejected(self):
        for aliases in ['shop', [''], ['UPPER'], ['a.b'], ['a/b'], ['a\nServerName evil'], ['same', 'same'], [None]]:
            with self.subTest(aliases=aliases), self.assertRaises(ValueError):
                control.validate_specification({'aliases': aliases})
        with self.assertRaises(ValueError):
            value = control.validate_specification({'subdomain': 'a' * 63, 'aliases': ['b']})
            control.environment_hostnames('abcdef012345', value, {'domain': 'example.test'})

    def test_alias_collisions_with_primary_hosts_are_rejected(self):
        first = control.validate_specification({'subdomain': 'demo', 'aliases': ['shop']})
        second = control.validate_specification({'subdomain': 'demo-shop'})
        with self.assertRaisesRegex(ValueError, 'same domain'):
            control.validate_domains({'abcdef012345': first, '123456abcdef': second}, {'domain': 'example.test'})

    def test_the_tunnel_vhost_includes_aliases_without_redirects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {'id': 'abcdef012345', 'hostname': 'abcdef012345.example.test',
                           'hostnames': ['abcdef012345.example.test', 'abcdef012345-shop.example.test'],
                           'document_root': str(root), 'php': '8.5', 'url': 'https://abcdef012345.example.test',
                           'path': str(root), 'engine': 'sqlite', 'password': 'b' * 48}
            with patch.object(control, 'SITES', root), patch.object(control, 'ENABLED', root), patch.object(control, 'STATE', root):
                control.vhost(environment)
            vhost = (root / ('lamp-' + environment['id'] + '.conf')).read_text()
            self.assertEqual(1, vhost.count('ServerAlias abcdef012345-shop.example.test'))
            self.assertNotIn('Redirect', vhost)
            self.assertNotIn('Rewrite', vhost)
            self.assertIn('SSLEngine on', vhost)
            self.assertIn('    SetEnv DB_CONNECTION "sqlite"\n', vhost)
            self.assertIn('    SetEnv DB_DATABASE "' + str(root / 'environments' / environment['id'] / 'data' / 'database.sqlite') + '"\n', vhost)
            self.assertEqual(1, vhost.count('SetEnv DB_PASSWORD'))
            self.assertIn('flushpackets=on', vhost)
            self.assertEqual(['https://' + host for host in environment['hostnames']], control.show(environment)['urls'])


if __name__ == '__main__':
    unittest.main()
