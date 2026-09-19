import copy
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class VisibilityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('CONFIGURATION', 'SETUP', 'STATE', 'PROJECTS', 'SITES', 'ENABLED'):
            path = self.root / name
            path.mkdir()
            mocked = patch.object(control, name, path)
            mocked.start()
            self.addCleanup(mocked.stop)
        (self.root / 'hosts').write_text('127.0.0.1 localhost\n')
        mocked = patch.object(control, 'HOSTS', self.root / 'hosts')
        mocked.start()
        self.addCleanup(mocked.stop)
        self.identity = 'abcdef012345'
        self.settings = {'domain': 'example.test'}
        self.value = control.validate_specification({'subdomain': 'demo', 'visibility': 'public'})
        self.desired = {self.identity: self.value}
        self.wildcard = {'id': 'wildcard', 'domain': '*.example.test', 'type': 'self_hosted', 'name': 'User policy'}
        self.apps = [self.wildcard]
        self.policies = {'wildcard': [{'decision': 'allow', 'include': [{'email': {'email': 'test@example.test'}}]}]}
        self.calls = []
        self.current = []
        self.real_wait_access = control.wait_access
        for name, arguments in [('cloudflare_request', {'side_effect': self.api}),
                                ('environments', {'side_effect': lambda: copy.deepcopy(self.current)}),
                                ('reload_apache', {}), ('wait_access', {})]:
            mocked = patch.object(control, name, **arguments)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)

    def api(self, method, resource, payload=None):
        self.calls.append((method, resource, copy.deepcopy(payload)))
        if method == 'GET' and resource.startswith('?'):
            return {'result': copy.deepcopy(self.apps), 'result_info': {'total_pages': 1}}
        identity = resource.split('?', 1)[0].strip('/').split('/')[0]
        if method == 'GET' and resource.split('?', 1)[0].endswith('/policies'):
            return {'result': copy.deepcopy(self.policies[identity])}
        if method == 'DELETE':
            self.apps = [app for app in self.apps if app['id'] != identity]
            self.policies.pop(identity, None)
        elif method in ('POST', 'PUT'):
            identity = identity or 'managed-' + str(len(self.apps))
            self.apps = [app for app in self.apps if app['id'] != identity]
            self.apps.append({**payload, 'id': identity})
            self.policies[identity] = copy.deepcopy(payload['policies'])
        else:
            raise AssertionError('Unexpected API operation')
        return {'result': {'id': identity}}

    def test_default_and_validation(self):
        self.assertEqual('private', control.validate_specification({})['visibility'])
        self.assertEqual('private', control.validate_specification({'visibility': None})['visibility'])
        for value in ('', 'PUBLIC', False, 1, []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'visibility'):
                control.validate_specification({'visibility': value})

    def test_public_creation_is_scoped_and_idempotent(self):
        before = copy.deepcopy(self.wildcard)
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual(2, len(self.apps))
        self.assertEqual('demo.example.test', self.apps[-1]['domain'])
        self.assertEqual('bypass', self.apps[-1]['policies'][0]['decision'])
        self.assertEqual([{'everyone': {}}], self.apps[-1]['policies'][0]['include'])
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual(1, sum(method == 'POST' for method, _, _ in self.calls))
        self.assertEqual(before, self.apps[0])

    def test_prepare_does_not_publish(self):
        control.sync_visibility(self.settings, self.desired, publish=False)
        self.assertEqual([self.wildcard], self.apps)

    def test_public_aliases_are_exact_scoped_destinations(self):
        self.value['aliases'] = ['one', 'two', 'three', 'four', 'five']
        control.sync_visibility(self.settings, self.desired)
        expected = {'demo.example.test', *(f'demo-{label}.example.test' for label in self.value['aliases'])}
        self.assertEqual(expected, control.access_destinations(self.apps[-1]))
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual(1, sum(method == 'POST' for method, _, _ in self.calls))
        self.value['visibility'] = 'private'
        self.wait_access.reset_mock()
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual([self.wildcard], self.apps)
        self.assertEqual(expected, {call.args[0] for call in self.wait_access.call_args_list})
        self.assertTrue(all(call.args[1] is False for call in self.wait_access.call_args_list))

    def test_public_subdomain_array_is_scoped_and_removed_hosts_are_protected(self):
        self.value['subdomain'] = ['demo', 'other', 'third']
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual({'demo.example.test', 'other.example.test', 'third.example.test'},
                         control.access_destinations(self.apps[-1]))
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual(1, sum(method == 'POST' for method, _, _ in self.calls))
        self.value['subdomain'] = ['demo', 'other']
        self.wait_access.reset_mock()
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual({'demo.example.test', 'other.example.test'}, control.access_destinations(self.apps[-1]))
        self.assertIn(('third.example.test', False), [call.args for call in self.wait_access.call_args_list])
        self.value['visibility'] = 'private'
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual([self.wildcard], self.apps)

    def test_foreign_application_on_array_secondary_host_is_not_overwritten(self):
        self.value['subdomain'] = ['demo', 'other']
        self.apps.append({'id': 'foreign', 'domain': 'other.example.test/admin'})
        with self.assertRaisesRegex(ValueError, 'separate Access application'):
            control.sync_visibility(self.settings, self.desired)
        self.assertTrue(all(method == 'GET' for method, _, _ in self.calls))

    def test_subdomain_list_changes_do_not_build_move_or_import(self):
        project = control.PROJECTS / 'demo'
        project.mkdir()
        (project / '.phprc').write_text('8.5\n')
        environment = {**self.value, 'id': self.identity, 'php': '8.5', 'path': str(project),
                       'status': 'ready', 'url': 'https://demo.example.test', 'hostname': 'demo.example.test',
                       'hostnames': ['demo.example.test'], 'project_owned': True,
                       'project_identity': [project.stat().st_dev, project.stat().st_ino],
                       'build_hash': None, 'applied': dict(self.value)}
        for labels in [['demo', 'other', 'third'], ['demo', 'third'], ['demo'], 'demo']:
            self.current = [environment]
            self.value['subdomain'] = labels
            with patch.object(control, 'sync_visibility'), patch.object(control, 'save_environment') as save, \
                 patch.object(control, 'vhost') as vhost, \
                 patch.object(control, 'add', side_effect=AssertionError('Unexpected build')), \
                 patch.object(control, 'database', side_effect=AssertionError('Unexpected database operation')):
                control.reconcile(self.settings, self.desired)
            expected = control.environment_hostnames(self.identity, self.value, self.settings)
            self.assertEqual(int(expected != environment['hostnames']), vhost.call_count)
            environment = copy.deepcopy(save.call_args.args[0])
            self.assertEqual(expected, environment['hostnames'])
            self.assertEqual(labels, environment['subdomain'])
            self.assertEqual(labels, environment['applied']['subdomain'])
            self.assertEqual(str(project), environment['path'])
        self.value['subdomain'] = ['other', 'demo']
        self.value['directory'] = 'other'
        self.current = [environment]
        with patch.object(control, 'sync_visibility') as sync, self.assertRaisesRegex(ValueError, 'project directory'):
            control.reconcile(self.settings, self.desired)
        sync.assert_not_called()

    def test_foreign_alias_application_is_not_ignored(self):
        self.value['aliases'] = ['shop']
        self.apps.append({'id': 'foreign', 'domain': 'demo-shop.example.test/admin'})
        with self.assertRaisesRegex(ValueError, 'separate Access application'):
            control.sync_visibility(self.settings, self.desired)
        self.assertTrue(all(method == 'GET' for method, _, _ in self.calls))

    def test_removed_public_aliases_are_not_left_bypassed(self):
        self.value['aliases'] = ['old']
        control.sync_visibility(self.settings, self.desired)
        self.value['aliases'] = ['new']
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual(2, len(self.apps))
        self.assertEqual({'demo.example.test', 'demo-new.example.test'}, control.access_destinations(self.apps[-1]))
        self.wait_access.assert_any_call('demo-old.example.test', False)

    def test_private_deletes_only_owned_exception(self):
        control.sync_visibility(self.settings, self.desired)
        self.value['visibility'] = 'private'
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual([self.wildcard], self.apps)
        self.wait_access.assert_called_with('demo.example.test', False)

    def test_orphaned_exception_is_removed_without_runtime_state(self):
        control.sync_visibility(self.settings, self.desired)
        control.sync_visibility(self.settings, {})
        self.assertEqual([self.wildcard], self.apps)

    def test_scoped_remove_does_not_delete_another_environment(self):
        control.sync_visibility(self.settings, self.desired)
        control.sync_visibility(self.settings, {}, identities={'123456abcdef'})
        self.assertEqual(2, len(self.apps))

    def test_reconciliation_cleans_exception_even_without_runtime_record(self):
        control.sync_visibility(self.settings, self.desired)
        self.assertEqual(2, len(self.apps))
        control.sync_visibility(self.settings, {})
        self.assertEqual([self.wildcard], self.apps)

    def test_foreign_specific_application_is_not_modified(self):
        foreign = {'id': 'foreign', 'name': 'Manual', 'domain': 'demo.example.test/admin'}
        self.apps.append(foreign)
        with self.assertRaisesRegex(ValueError, 'separate Access application'):
            control.sync_visibility(self.settings, self.desired)
        self.assertEqual(foreign, self.apps[-1])
        self.assertTrue(all(method == 'GET' for method, _, _ in self.calls))

    def test_add_cannot_ignore_another_managed_application_on_its_host(self):
        control.sync_visibility(self.settings, self.desired)
        with self.assertRaisesRegex(ValueError, 'separate Access application'):
            control.sync_visibility(self.settings, {'123456abcdef': self.value}, identities={'123456abcdef'})

    def test_destinations_are_checked_even_when_not_the_primary_domain(self):
        self.apps.append({'id': 'foreign', 'name': 'Manual', 'domain': 'other.test',
                          'destinations': [{'type': 'public', 'uri': 'demo.example.test'}]})
        with self.assertRaisesRegex(ValueError, 'separate Access application'):
            control.sync_visibility(self.settings, self.desired)

    def test_wildcard_bypass_is_rejected(self):
        self.policies['wildcard'] = [{'decision': 'bypass'}]
        with self.assertRaisesRegex(ValueError, 'no Bypass'):
            control.sync_visibility(self.settings, self.desired)

    def test_missing_or_duplicate_wildcard_is_rejected(self):
        for apps in ([], [self.wildcard, dict(self.wildcard, id='duplicate')]):
            self.apps = apps
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                control.sync_visibility(self.settings, self.desired)

    def test_api_failure_disables_origin(self):
        site = control.ENABLED / ('lamp-' + self.identity + '.conf')
        site.touch()
        self.cloudflare_request.side_effect = RuntimeError('API unavailable')
        with self.assertRaisesRegex(RuntimeError, 'API unavailable'):
            control.sync_visibility(self.settings, self.desired)
        self.assertFalse(site.exists())

    def test_failed_private_propagation_keeps_origin_disabled(self):
        control.sync_visibility(self.settings, self.desired)
        site = control.ENABLED / ('lamp-' + self.identity + '.conf')
        site.touch()
        self.value['visibility'] = 'private'
        self.wait_access.side_effect = RuntimeError('Propagation failed')
        with self.assertRaisesRegex(RuntimeError, 'Propagation failed'):
            control.sync_visibility(self.settings, self.desired)
        self.assertFalse(site.exists())

    def test_empty_or_changed_managed_policy_is_repaired_without_duplicate(self):
        control.sync_visibility(self.settings, self.desired)
        identity = self.apps[-1]['id']
        for policies in ([], [{'decision': 'allow'}]):
            self.policies[identity] = policies
            control.sync_visibility(self.settings, self.desired)
            self.assertEqual(2, len(self.apps))
            self.assertEqual('bypass', self.policies[identity][0]['decision'])

    def test_all_pages_are_read(self):
        api = self.api
        def paginated(method, resource, payload=None):
            if resource == '?page=1&per_page=100':
                return {'result': [], 'result_info': {'total_pages': 2}}
            return api(method, resource, payload)
        self.cloudflare_request.side_effect = paginated
        control.sync_visibility(self.settings, self.desired)
        self.assertIn(('GET', '?page=2&per_page=100', None), self.calls)

    def test_visibility_only_change_does_not_rebuild(self):
        project = control.PROJECTS / 'demo'
        project.mkdir()
        (project / '.phprc').write_text('8.5\n')
        environment = {**self.value, 'visibility': 'private', 'id': self.identity, 'php': '8.5',
                       'path': str(project), 'status': 'ready', 'url': 'https://demo.example.test',
                       'hostname': 'demo.example.test', 'project_owned': False,
                       'applied': dict(self.value, visibility='private')}
        self.current = [environment]
        with patch.object(control, 'sync_visibility'), patch.object(control, 'save_environment') as save, \
             patch.object(control, 'add', side_effect=AssertionError('Unexpected build')), \
             patch.object(control, 'database', side_effect=AssertionError('Unexpected database operation')):
            control.reconcile(self.settings, self.desired)
        self.assertEqual('public', save.call_args.args[0]['visibility'])

    def test_service_auth_everyone_is_not_accepted_as_wildcard_protection(self):
        self.policies['wildcard'] = [{'decision': 'non_identity', 'include': [{'everyone': {}}]}]
        with self.assertRaisesRegex(ValueError, 'no Bypass'):
            control.sync_visibility(self.settings, self.desired)

    def test_alias_only_change_does_not_rebuild_or_touch_databases(self):
        project = control.PROJECTS / 'demo'
        project.mkdir()
        (project / '.phprc').write_text('8.5\n')
        environment = {**self.value, 'id': self.identity, 'php': '8.5', 'path': str(project),
                       'status': 'ready', 'url': 'https://demo.example.test', 'hostname': 'demo.example.test',
                       'project_owned': False, 'applied': dict(self.value)}
        self.current = [environment]
        self.value['aliases'] = ['shop']
        with patch.object(control, 'sync_visibility'), patch.object(control, 'save_environment') as save, \
             patch.object(control, 'vhost') as vhost, \
             patch.object(control, 'add', side_effect=AssertionError('Unexpected build')), \
             patch.object(control, 'database', side_effect=AssertionError('Unexpected database operation')):
            control.reconcile(self.settings, self.desired)
        self.assertEqual(['demo.example.test', 'demo-shop.example.test'], save.call_args.args[0]['hostnames'])
        self.assertEqual(['shop'], save.call_args.args[0]['applied']['aliases'])
        vhost.assert_called_once()

    def test_private_probe_requires_cloudflare_login_not_an_origin_denial(self):
        with patch.object(control.http.client, 'HTTPSConnection') as connection, \
             patch.object(control.time, 'monotonic', side_effect=[0, 121]):
            response = connection.return_value.getresponse.return_value
            response.status = 403
            response.getheader.return_value = ''
            with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
                self.real_wait_access('demo.example.test', False)


    def test_public_probe_accepts_an_origin_error_but_waits_for_the_cloudflare_edge(self):
        with patch.object(control.http.client, 'HTTPSConnection') as connection, \
             patch.object(control.time, 'monotonic', side_effect=[0, 0, 121]):
            response = connection.return_value.getresponse.return_value
            response.getheader.return_value = ''
            response.status = 503
            self.real_wait_access('demo.example.test', True)
            response.status = 530
            with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
                self.real_wait_access('demo.example.test', True)


class CloudflareRequestTest(unittest.TestCase):
    def test_api_errors_never_expose_response_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control, 'CONFIGURATION', Path(directory)), \
             patch.object(control, 'SETUP', Path(directory)), patch.object(control, 'STATE', Path(directory)), \
             patch.object(control.http.client, 'HTTPSConnection') as connection:
            folder = Path(directory) / 'cloudflare'
            folder.mkdir()
            (Path(directory) / 'setup.yaml').write_text('domain: example.test\n')
            (Path(directory) / 'settings.yaml').write_text('cloudflare:\n  token: test-secret\n  email: jane@example.test\n')
            (folder / 'cloudflared-credentials.json').write_text('{"AccountTag":"' + 'a' * 32 + '"}')
            response = connection.return_value.getresponse.return_value
            response.status = 403
            response.read.return_value = b'{"success":false,"errors":[{"message":"test-secret"}]}'
            with self.assertRaisesRegex(RuntimeError, 'HTTP 403') as failure:
                control.cloudflare_request('POST', '', {})
            self.assertNotIn('test-secret', str(failure.exception))
            connection.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
