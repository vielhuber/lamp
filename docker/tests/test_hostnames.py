import importlib.util
import re
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

    def test_invalid_subdomain_lists_are_rejected(self):
        for labels in [[], [''], ['valid', None], ['valid', 42], ['valid', []], ['valid', 'UPPER'],
                       ['valid', 'a.b'], ['valid', '../path'], ['valid', 'line\nHost'],
                       ['valid', 'phpmyadmin'], ['same', 'same'], ['valid', 'a' * 64]]:
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                control.validate_specification({'subdomain': labels})

    def test_subdomain_list_roundtrips_through_yaml(self):
        identity = 'abcdef012345'
        value = control.validate_specification({'subdomain': ['one', 'two']})
        with tempfile.TemporaryDirectory() as directory, patch.object(control, 'CONFIGURATION', Path(directory)):
            control.write_desired([value], None)
            entries, original = control.read_desired()
            self.assertEqual(['one', 'two'], entries[0]['subdomain'])
            self.assertEqual(original, (Path(directory) / 'environments.yaml').read_bytes())

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

    def test_explicit_hosts_reach_tls_vhosts_and_show(self):
        identity = '0123456789ab'
        value = control.validate_specification({'subdomain': ['site', 'shop', 'blog', 'events', 'press', 'jobs']})
        settings = {'domain': 'example.test'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {**value, 'id': identity, 'hostname': control.environment_hostname(identity, value, settings),
                           'hostnames': control.environment_hostnames(identity, value, settings),
                           'url': control.environment_url(identity, value, settings),
                           'document_root': str(root / 'html/site'), 'php': '8.4'}
            (root / 'environments' / identity).mkdir(parents=True)
            with patch.object(control, 'STATE', root), patch.object(control, 'CONFIGURATION', root), \
                 patch.object(control, 'SITES', root), patch.object(control, 'ENABLED', root), patch.object(control, 'run'):
                control.certificate(environment)
                control.vhost(environment)
            tls = (root / 'environments' / identity / 'tls.ext').read_text()
            vhost = (root / ('lamp-' + identity + '.conf')).read_text()
            self.assertIn('subjectAltName=' + ','.join('DNS:' + host for host in environment['hostnames']) + '\n', tls)
            self.assertEqual(3, vhost.count('ServerAlias ' + ' '.join(environment['hostnames'][1:])))
            self.assertEqual(2, vhost.count('DocumentRoot "' + environment['document_root'] + '"'))
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

    def test_tls_and_all_vhosts_include_aliases_without_canonical_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {'id': 'abcdef012345', 'hostname': 'abcdef012345.example.test',
                           'hostnames': ['abcdef012345.example.test', 'abcdef012345-shop.example.test'],
                           'document_root': str(root), 'php': '8.5', 'url': 'https://abcdef012345.example.test'}
            (root / 'environments' / environment['id']).mkdir(parents=True)
            with patch.object(control, 'STATE', root), patch.object(control, 'CONFIGURATION', root), \
                 patch.object(control, 'SITES', root), patch.object(control, 'ENABLED', root), patch.object(control, 'run'):
                control.certificate(environment)
                control.vhost(environment)
            tls = (root / 'environments' / environment['id'] / 'tls.ext').read_text()
            self.assertIn('DNS:abcdef012345.example.test,DNS:abcdef012345-shop.example.test', tls)
            vhost = (root / ('lamp-' + environment['id'] + '.conf')).read_text()
            self.assertEqual(3, vhost.count('ServerAlias abcdef012345-shop.example.test'))
            self.assertIn('https://%1%{REQUEST_URI}', vhost)
            pattern = next(line.split()[2] for line in vhost.splitlines() if 'RewriteCond %{HTTP_HOST}' in line)
            for host in environment['hostnames']:
                self.assertEqual(host, re.fullmatch(pattern, host + ':8443').group(1))
            self.assertIsNone(re.fullmatch(pattern, 'abcdef012345-shop.example.test.evil.test'))
            self.assertIsNone(re.fullmatch(pattern, 'foreign.example.test'))
            self.assertIn('flushpackets=on', vhost)
            self.assertEqual(['https://' + host for host in environment['hostnames']], control.show(environment)['urls'])


if __name__ == '__main__':
    unittest.main()
