import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

spec = importlib.util.spec_from_file_location('lamp_cloudflare', Path(__file__).parents[1] / 'scripts/cloudflare.py')
cloudflare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloudflare)


class FakeCloudflare:
    """In-memory stand-in for the parts of the api that cloudflare-setup touches."""

    def __init__(self):
        self.tunnels, self.records, self.tokens, self.apps, self.policies, self.rules = [], [], [], [], {}, None
        self.writes = []
        self.counter = 0

    def identity(self):
        self.counter += 1
        return f'id-{self.counter}'

    def __call__(self, token, method, path, payload=None, missing=False):
        assert token == 'test-token'
        route = path.split('?')[0]
        if method != 'GET':
            self.writes.append((method, route))
        if route == '/zones':
            return [{'id': 'zone-1', 'name': 'example.test', 'account': {'id': 'account-1'}}]
        if route == '/accounts/account-1/cfd_tunnel' and method == 'GET':
            return list(self.tunnels)
        if route == '/accounts/account-1/cfd_tunnel' and method == 'POST':
            tunnel = {'id': self.identity(), 'name': payload['name']}
            self.tunnels.append(tunnel)
            return tunnel
        if route.startswith('/accounts/account-1/cfd_tunnel/') and method == 'DELETE':
            if not route.endswith('/connections'):
                self.tunnels = [tunnel for tunnel in self.tunnels if tunnel['id'] != route.rsplit('/', 1)[1]]
            return {}
        if route == '/zones/zone-1/dns_records' and method == 'GET':
            return list(self.records)
        if route == '/zones/zone-1/dns_records' and method == 'POST':
            self.records.append({**payload, 'id': self.identity()})
            return self.records[-1]
        if route.startswith('/zones/zone-1/dns_records/') and method == 'PUT':
            self.records = [{**payload, 'id': record['id']} if record['id'] == route.rsplit('/', 1)[1] else record for record in self.records]
            return {}
        if route == '/accounts/account-1/access/service_tokens' and method == 'GET':
            return [{key: value for key, value in item.items() if key != 'client_secret'} for item in self.tokens]
        if route == '/accounts/account-1/access/service_tokens' and method == 'POST':
            self.tokens.append({'id': self.identity(), 'name': payload['name'], 'client_id': self.identity() + '.access', 'client_secret': 'secret-' + self.identity()})
            return self.tokens[-1]
        if route.endswith('/rotate'):
            for item in self.tokens:
                if item['id'] == route.split('/')[-2]:
                    item['client_secret'] = 'secret-' + self.identity()
                    return item
        if route == '/accounts/account-1/access/apps' and method == 'GET':
            return list(self.apps)
        if route == '/accounts/account-1/access/apps' and method == 'POST':
            app = {key: value for key, value in payload.items() if key != 'policies'}
            app['id'] = self.identity()
            self.apps.append(app)
            self.policies[app['id']] = [{**policy, 'id': self.identity()} for policy in payload['policies']]
            return app
        if route.startswith('/accounts/account-1/access/apps/') and route.endswith('/policies'):
            return list(self.policies[route.split('/')[-2]])
        if route.startswith('/accounts/account-1/access/apps/') and method == 'PUT':
            identity = route.rsplit('/', 1)[1]
            self.apps = [{**{key: value for key, value in payload.items() if key != 'policies'}, 'id': identity} if app['id'] == identity else app for app in self.apps]
            self.policies[identity] = [{**policy, 'id': policy.get('id') or self.identity()} for policy in payload['policies']]
            return {}
        if route.endswith('/http_request_cache_settings/entrypoint') and method == 'GET':
            assert missing
            return self.rules
        if route.endswith('/http_request_cache_settings/entrypoint') and method == 'PUT':
            self.rules = {'rules': [{**rule, 'id': rule.get('id') or self.identity(), 'last_updated': 'now'} for rule in payload['rules']]}
            return self.rules
        raise AssertionError(f'Unexpected call {method} {route}')


class CloudflareSetupTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name) / 'cloudflare'
        self.api = FakeCloudflare()
        mocked = patch.object(cloudflare, 'request', self.api)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.settings = {'domain': 'example.test', 'cloudflare': {'token': 'test-token', 'email': 'jane@example.test'}}

    def test_first_run_creates_everything_and_a_rerun_changes_nothing(self):
        self.api.rules = {'rules': [{'id': 'keep', 'description': 'other', 'expression': 'true', 'action': 'set_cache_settings', 'action_parameters': {'cache': True}, 'enabled': True, 'last_updated': 'x'}]}
        results = cloudflare.setup(self.settings, self.folder)
        self.assertEqual({'tunnel example.test': 'created', 'dns *.example.test': 'created', 'service token lamp': 'created',
                          'access application lamp': 'created', 'cache rule': 'created'}, results)
        credentials = json.loads((self.folder / 'cloudflared-credentials.json').read_text())
        self.assertEqual({'AccountTag', 'TunnelSecret', 'TunnelID', 'TunnelName'}, set(credentials))
        self.assertEqual(self.api.tunnels[0]['id'], credentials['TunnelID'])
        self.assertEqual(0o600, (self.folder / 'cloudflared-credentials.json').stat().st_mode & 0o777)
        self.assertEqual([{'type': 'CNAME', 'name': '*.example.test', 'content': credentials['TunnelID'] + '.cfargotunnel.com', 'proxied': True, 'ttl': 1, 'id': self.api.records[0]['id']}], self.api.records)
        service = yaml.safe_load((self.folder / 'cloudflare-service-token.yaml').read_text())
        self.assertEqual(self.api.tokens[0]['client_id'], service['CF-Access-Client-Id'])
        self.assertEqual(self.api.tokens[0]['client_secret'], service['CF-Access-Client-Secret'])
        app = self.api.apps[0]
        self.assertEqual(('lamp', '*.example.test', 'self_hosted'), (app['name'], app['domain'], app['type']))
        policies = self.api.policies[app['id']]
        self.assertEqual([('developer', 'allow', [{'email': {'email': 'jane@example.test'}}]), ('harness', 'non_identity', [{'service_token': {'token_id': self.api.tokens[0]['id']}}])],
                         [(policy['name'], policy['decision'], policy['include']) for policy in policies])
        self.assertEqual(['other', 'lamp: bypass cache'], [rule['description'] for rule in self.api.rules['rules']])
        self.assertEqual({'cache': False}, self.api.rules['rules'][1]['action_parameters'])
        self.api.writes.clear()
        self.assertEqual({name: 'ok' for name in results}, cloudflare.setup(self.settings, self.folder))
        self.assertEqual([], self.api.writes)
        self.assertEqual(credentials, json.loads((self.folder / 'cloudflared-credentials.json').read_text()))

    def test_lost_local_secrets_recreate_the_tunnel_and_rotate_the_service_token(self):
        cloudflare.setup(self.settings, self.folder)
        old_tunnel = self.api.tunnels[0]['id']
        old_secret = self.api.tokens[0]['client_secret']
        (self.folder / 'cloudflared-credentials.json').unlink()
        (self.folder / 'cloudflare-service-token.yaml').unlink()
        results = cloudflare.setup(self.settings, self.folder)
        self.assertEqual({'tunnel example.test': 'recreated', 'dns *.example.test': 'updated', 'service token lamp': 'rotated',
                          'access application lamp': 'ok', 'cache rule': 'ok'}, results)
        self.assertEqual(1, len(self.api.tunnels))
        self.assertNotEqual(old_tunnel, self.api.tunnels[0]['id'])
        self.assertEqual(self.api.tunnels[0]['id'] + '.cfargotunnel.com', self.api.records[0]['content'])
        self.assertEqual(1, len(self.api.tokens))
        self.assertNotEqual(old_secret, yaml.safe_load((self.folder / 'cloudflare-service-token.yaml').read_text())['CF-Access-Client-Secret'])

    def test_drifted_application_and_rule_are_restored_without_duplicate_policies(self):
        cloudflare.setup(self.settings, self.folder)
        app = self.api.apps[0]
        app['destinations'].append({'type': 'public', 'uri': '*.other.test'})
        self.api.policies[app['id']][0]['include'] = [{'everyone': {}}]
        self.api.rules['rules'][0]['action_parameters'] = {'cache': True}
        results = cloudflare.setup(self.settings, self.folder)
        self.assertEqual('updated', results['access application lamp'])
        self.assertEqual('updated', results['cache rule'])
        self.assertEqual(1, len(self.api.apps))
        self.assertEqual([{'type': 'public', 'uri': '*.example.test'}], self.api.apps[0]['destinations'])
        self.assertEqual(2, len(self.api.policies[app['id']]))
        self.assertEqual([{'email': {'email': 'jane@example.test'}}], self.api.policies[app['id']][0]['include'])
        self.assertEqual(1, len(self.api.rules['rules']))
        self.assertEqual({name: 'ok' for name in results}, cloudflare.setup(self.settings, self.folder))

    def test_missing_settings_or_zone_fail_before_any_write(self):
        with self.assertRaisesRegex(ValueError, 'cloudflare.token'):
            cloudflare.setup({'domain': 'example.test'}, self.folder)
        with self.assertRaisesRegex(ValueError, 'exactly one zone'):
            cloudflare.setup({'domain': 'other.test', 'cloudflare': self.settings['cloudflare']}, self.folder)
        self.assertEqual([], self.api.writes)


if __name__ == '__main__':
    unittest.main()
