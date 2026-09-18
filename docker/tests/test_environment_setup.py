import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lamp_control', Path(__file__).parents[1] / 'scripts/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class EnvironmentSetupTest(unittest.TestCase):
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
        self.identity = 'abcdef012345'

    def invoke(self, *arguments):
        exists = Path.exists
        output = io.StringIO()
        with patch.object(control.sys, 'argv', ['control', *arguments]), \
             patch.object(Path, 'exists', lambda path: str(path) == '/.dockerenv' or exists(path)), \
             contextlib.redirect_stdout(output):
            control.main()
        return output.getvalue()

    def execute_build(self, arguments, **options):
        if arguments[:2] == ['bash', '-c']:
            output = options.get('output')
            result = subprocess.run(arguments, cwd=options['cwd'], env=options['environment'],
                                    stdout=output or subprocess.DEVNULL, stderr=output or subprocess.DEVNULL)
            if result.returncode:
                raise RuntimeError('Test build failed; output withheld.')
        return ''

    def test_existing_directory_is_adopted_without_build_until_build_is_requested(self):
        project = control.PROJECTS / 'existing'
        project.mkdir()
        original = project / 'original.txt'
        original.write_text('keep')
        build = 'test "$DB_PASSWORD" = rootpw && test "$DB_DATABASE" = existing && test "$DB_USERNAME" = root && printf built > build-result.txt'
        self.run.side_effect = self.execute_build
        self.invoke('add', '--id', self.identity, '--subdomain', 'existing', '--db-name', 'existing', '--db-engine', 'mysql', '--build', build)
        environment = control.load_environment(self.identity)
        self.assertFalse((project / 'build-result.txt').exists())
        self.assertEqual('ready', environment['status'])
        self.invoke('build', self.identity)
        self.assertEqual('built', (project / 'build-result.txt').read_text())
        self.assertFalse(environment['project_owned'])
        self.assertTrue(environment['databases_ready'])
        self.assertFalse(environment.get('mysql_owned'))
        self.assertEqual(('mysql', 'existing'), (environment['engine'], environment['database']))
        self.assertIn('CREATE DATABASE IF NOT EXISTS `existing`;', ''.join(call.kwargs.get('input', '') for call in self.run.call_args_list))
        self.assertTrue(Path(environment['setup_environment']).is_file())
        self.assertEqual('keep', original.read_text())
        self.run.reset_mock()
        self.invoke('add', '--id', self.identity)
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))
        self.assertFalse(any(call.args[0][0] in ('bash', 'chown', 'mysql', 'psql') for call in self.run.call_args_list))

    def test_yaml_add_uses_repository_script_without_changing_existing_git_checkout(self):
        project = control.PROJECTS / 'existing'
        project.mkdir()
        remote = 'git@example.test:owner/project.git'
        script = control.build_script(remote)
        script.parent.mkdir()
        script.write_text('printf built > build-result.txt\n')
        value = control.validate_specification({'git': remote, 'subdomain': 'existing'})
        control.write_desired([value], None)
        self.run.side_effect = self.execute_build
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))
        self.assertFalse((project / 'build-result.txt').exists())
        self.assertFalse(any(call.args[0][0] in ('git', 'chown', 'bash') for call in self.run.call_args_list))
        script.write_text('printf changed > build-result.txt\n')
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))
        self.assertFalse((project / 'build-result.txt').exists())
        identity = control.environments()[0]['id']
        self.invoke('build', identity)
        self.assertEqual('changed', (project / 'build-result.txt').read_text())
        self.assertEqual('ready', control.load_environment(identity)['status'])

    def test_failed_project_build_retries_without_recreating_databases(self):
        project = control.PROJECTS / 'created'
        value = control.validate_specification({'subdomain': 'created', 'build': 'exit 1'})
        self.run.side_effect = self.execute_build
        with self.assertRaisesRegex(RuntimeError, 'Test build failed'):
            control.reconcile(self.settings, {self.identity: value})
        self.assertEqual('failed', control.load_environment(self.identity)['status'])
        value['build'] = 'printf recovered > build-result.txt'
        self.run.reset_mock()
        control.reconcile(self.settings, {self.identity: value})
        self.assertEqual('recovered', (project / 'build-result.txt').read_text())
        self.assertFalse(any(call.args[0][0] in ('mysql', 'psql') for call in self.run.call_args_list))

    def test_static_directories_are_never_deleted_even_after_builds(self):
        for adopted in (True, False):
            with self.subTest(adopted=adopted):
                label = 'existing' if adopted else 'created'
                project = control.PROJECTS / label
                if adopted:
                    project.mkdir()
                value = control.validate_specification({'subdomain': [label, label + '-alias'], 'build': 'printf built > keep.txt'})
                self.run.side_effect = self.execute_build
                control.reconcile(self.settings, {self.identity: value})
                self.run.reset_mock()
                if adopted:
                    control.reconcile(self.settings, {})
                else:
                    control.remove(self.identity)
                if adopted:
                    self.assertFalse((project / 'keep.txt').exists())
                else:
                    self.assertEqual('built', (project / 'keep.txt').read_text())
                self.assertTrue(project.is_dir())
                self.assertEqual(0, len(control.environments()))
                self.assertEqual(0, sum('DROP DATABASE' in call.kwargs.get('input', '') for call in self.run.call_args_list))

    def test_dynamic_directories_are_deleted_whether_created_or_adopted(self):
        for adopted in (True, False):
            with self.subTest(adopted=adopted):
                project = control.PROJECTS / '_environments' / self.identity
                if adopted:
                    project.mkdir(parents=True)
                control.reconcile(self.settings, {self.identity: control.validate_specification({})})
                (project / 'temporary.txt').write_text('temporary data')
                control.remove(self.identity)
                self.assertFalse(project.exists())
                self.assertTrue(project.parent.is_dir())

    def test_replaced_adopted_dynamic_directory_cannot_be_deleted(self):
        project = control.PROJECTS / '_environments' / self.identity
        project.mkdir(parents=True)
        control.reconcile(self.settings, {self.identity: control.validate_specification({})})
        project.rename(project.with_name('original'))
        project.mkdir()
        source = project / 'keep.txt'
        source.write_text('foreign replacement')
        with self.assertRaisesRegex(ValueError, 'replaced'):
            control.remove(self.identity)
        self.assertEqual('foreign replacement', source.read_text())

    def test_legacy_adopted_environment_does_not_build_on_unchanged_start(self):
        project = control.PROJECTS / 'legacy'
        project.mkdir()
        value = control.validate_specification({'subdomain': 'legacy', 'build': 'printf setup'})
        control.reconcile(self.settings, {self.identity: value})
        environment = control.load_environment(self.identity)
        for key in ('engine', 'database', 'setup_environment', 'build_hash', 'mysql_database', 'postgres_database'):
            environment[key] = None
        environment['mysql_owned'] = environment['postgres_owned'] = environment['databases_ready'] = False
        control.save_environment(environment)
        with patch.object(control, 'add') as add:
            control.reconcile(self.settings, {self.identity: value})
        add.assert_not_called()

    def test_build_command_reruns_configured_build_for_legacy_adopted_environment(self):
        project = control.PROJECTS / 'legacy'
        project.mkdir()
        value = control.validate_specification({'subdomain': 'legacy', 'build': 'printf built > build-result.txt'})
        control.write_desired([value], None)
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))
        environment = control.environments()[0]
        identity = environment['id']
        for key in ('engine', 'database', 'setup_environment', 'build_hash', 'mysql_database', 'postgres_database'):
            environment[key] = None
        environment['mysql_owned'] = environment['postgres_owned'] = environment['databases_ready'] = False
        control.save_environment(environment)
        self.run.side_effect = self.execute_build
        result = json.loads(self.invoke('build', identity))
        environment = control.load_environment(identity)
        self.assertEqual('built', (project / 'build-result.txt').read_text())
        self.assertEqual('ready', result['status'])
        self.assertTrue(environment['databases_ready'])
        self.assertTrue(Path(environment['setup_environment']).is_file())
        (project / 'build-result.txt').unlink()
        self.run.reset_mock()
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))
        self.assertFalse((project / 'build-result.txt').exists())
        self.assertFalse(any(call.args[0][0] in ('bash', 'mysql', 'psql') for call in self.run.call_args_list))
        self.invoke('build', identity)
        self.assertEqual('built', (project / 'build-result.txt').read_text())
        self.assertFalse(any(call.args[0][0] in ('mysql', 'psql') for call in self.run.call_args_list))

    def test_static_databases_are_fixed_never_dropped_and_imported_with_root(self):
        (control.PROJECTS / 'shop').mkdir()
        self.assertEqual('shop', json.loads(self.invoke('add', '--subdomain', 'shop', '--db-name', 'shop', '--db-engine', 'postgres'))['database'])
        environment = control.environments()[0]
        inputs = ''.join(call.kwargs.get('input', '') for call in self.run.call_args_list)
        self.assertIn("SELECT datname FROM pg_database WHERE datname='shop';", inputs)
        self.assertIn('CREATE DATABASE "shop";', inputs)
        self.assertNotIn('lamp_', inputs)
        setup = Path(environment['setup_environment']).read_text()
        self.assertIn("export DB_CONNECTION=pgsql\n", setup)
        self.assertIn("export PGDATABASE=shop\n", setup)
        self.assertIn("export PGUSER=postgres\n", setup)
        self.assertIn("export DB_PASSWORD=rootpw\n", setup)
        (control.CONFIGURATION / 'syncdb').mkdir()
        (control.CONFIGURATION / 'syncdb' / 'shop.json').write_text(json.dumps({'engine': 'mysql', 'source': {}}))
        with patch.object(control, 'run_sync') as sync, self.assertRaisesRegex(ValueError, 'profile is for mysql'):
            control.sync_database(environment, 'shop')
        sync.assert_not_called()
        self.run.reset_mock()
        control.remove(environment['id'])
        self.assertNotIn('DROP DATABASE', ''.join(call.kwargs.get('input', '') for call in self.run.call_args_list))
        (control.PROJECTS / 'blog').mkdir()
        self.invoke('add', '--subdomain', 'blog', '--db-name', 'blog', '--db-engine', 'mysql')
        environment = control.environments()[0]
        (control.CONFIGURATION / 'syncdb' / 'blog.json').write_text(json.dumps({'engine': 'mysql', 'source': {}}))
        with patch.object(control, 'run_sync') as sync:
            control.sync_database(environment, 'blog')
        self.assertEqual({'host': 'localhost', 'port': '3306', 'database': 'blog', 'username': 'root', 'password': 'rootpw',
                          'ssh': False, 'cmd': 'mysql', 'sql_log_bin': False}, sync.call_args.args[1]['target'])
        for arguments in (['--subdomain', 'x', '--db-name', 'x'], ['--db-name', 'x', '--db-engine', 'mysql'], ['--subdomain', 'x', '--db-name', 'a b', '--db-engine', 'mysql']):
    def test_commands_accept_a_subdomain_instead_of_the_id(self):
        (control.PROJECTS / 'site').mkdir()
        identity = json.loads(self.invoke('add', '--subdomain', ['site', 'shop'][0], '--alias', 'shop'))['id']
        self.assertEqual(identity, json.loads(self.invoke('show', 'site'))['id'])
        self.assertEqual(identity, control.resolve_identity(identity))
        for value in ('missing', 'shop', 'Site', 'a b'):
            with self.subTest(value=value), self.assertRaises((ValueError, SystemExit)):
                self.invoke('show', value)
        self.invoke('remove', 'site')
        self.assertEqual([], control.environments())

    def test_syncdb_command_runs_the_profile_unchanged(self):
        (control.CONFIGURATION / 'syncdb').mkdir()
        (control.STATE / 'syncdb').mkdir()
        with self.assertRaisesRegex(ValueError, 'No syncdb profile'):
            self.invoke('sync', 'shop-production-local')
        (control.CONFIGURATION / 'syncdb' / 'shop-production-local.json').write_text('{"target": {"database": "shop", "password": "root"}}')
        seen = {}
        def capture(arguments, **options):
            if arguments[:1] == ['php8.5']:
                seen['arguments'] = arguments
                seen['profile'] = (control.STATE / 'syncdb' / 'shop-production-local.json').read_text()
            return ''
        self.run.side_effect = capture
        self.assertEqual({'profile': 'shop-production-local', 'status': 'imported'}, json.loads(self.invoke('sync', 'shop-production-local')))
        self.assertEqual('shop-production-local', seen['arguments'][-1])
        self.assertEqual('{"target": {"database": "shop", "password": "root"}}', seen['profile'])
        self.assertFalse((control.STATE / 'syncdb' / 'shop-production-local.json').exists())
        with self.subTest('traversal'), self.assertRaises((ValueError, SystemExit)):
            self.invoke('sync', '../settings')

    def test_hosts_file_lists_every_environment_hostname(self):
        (control.PROJECTS / 'site').mkdir()
        identity = json.loads(self.invoke('add', '--subdomain', 'site', '--alias', 'shop'))['id']
        self.assertEqual(['127.0.0.1 localhost', '127.0.0.1 site-shop.example.test # lamp', '127.0.0.1 site.example.test # lamp'],
                         control.HOSTS.read_text().splitlines())
        self.invoke('remove', identity)
        self.assertEqual(['127.0.0.1 localhost'], control.HOSTS.read_text().splitlines())

    def test_certificate_is_requested_once_and_renewed_afterwards(self):
        settings = {'domain': 'example.test', 'cloudflare': {'token': 'test-token', 'email': 'jane@example.test'}}
        with self.assertRaisesRegex(ValueError, 'cloudflare.token'):
            control.ensure_certificate({'domain': 'example.test'})
        with patch.object(control, 'LETSENCRYPT', control.STATE / 'letsencrypt'):
            control.ensure_certificate(settings)
            certbot = [call.args[0] for call in self.run.call_args_list if call.args[0][0] == 'certbot']
            self.assertEqual(['certbot', 'certonly', '--non-interactive', '--agree-tos', '--email', 'jane@example.test', '--dns-cloudflare',
                              '--dns-cloudflare-credentials', str(control.STATE / 'secrets' / 'cloudflare.ini'), '--dns-cloudflare-propagation-seconds', '30',
                              '--cert-name', 'example.test', '-d', 'example.test', '-d', '*.example.test'], certbot[-1])
            self.assertEqual('dns_cloudflare_api_token = test-token\n', (control.STATE / 'secrets' / 'cloudflare.ini').read_text())
            self.assertEqual(0o600, (control.STATE / 'secrets' / 'cloudflare.ini').stat().st_mode & 0o777)
            (control.STATE / 'letsencrypt' / 'live' / 'example.test').mkdir(parents=True)
            (control.STATE / 'letsencrypt' / 'live' / 'example.test' / 'fullchain.pem').write_text('cert')
            self.run.reset_mock()
            control.ensure_certificate(settings)
            self.assertEqual([['certbot', 'renew', '--non-interactive', '--quiet']], [call.args[0] for call in self.run.call_args_list if call.args[0][0] == 'certbot'])

            with self.subTest(arguments=arguments), self.assertRaises((ValueError, SystemExit)):
                self.invoke('add', *arguments)
        (control.PROJECTS / 'plain').mkdir()
        self.invoke('add', '--subdomain', 'plain')
        plain = [item for item in control.environments() if item['subdomain'] == 'plain'][0]
        self.assertNotIn('DB_', Path(plain['setup_environment']).read_text())
        self.assertIsNone(plain['database'])

    def test_build_output_is_written_to_a_private_log_named_in_the_failure(self):
        project = control.PROJECTS / 'logged'
        value = control.validate_specification({'subdomain': 'logged', 'build': 'echo progress; echo problem >&2; exit 3'})
        self.run.side_effect = self.execute_build
        with self.assertRaisesRegex(RuntimeError, 'Build log: .*/environments/abcdef012345/build.log'):
            control.reconcile(self.settings, {self.identity: value})
        log = control.STATE / 'environments' / self.identity / 'build.log'
        self.assertEqual('progress\nproblem\n', log.read_text())
        self.assertEqual(0o600, log.stat().st_mode & 0o777)
        value['build'] = 'echo fixed'
        control.reconcile(self.settings, {self.identity: value})
        self.assertEqual('fixed\n', log.read_text())
        self.assertEqual('ready', control.load_environment(self.identity)['status'])

    def test_build_command_requires_configured_build_and_known_environment(self):
        control.write_desired([control.validate_specification({'subdomain': 'plain'})], None)
        (control.PROJECTS / 'plain').mkdir()
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))
        with self.assertRaisesRegex(ValueError, 'has no build'):
            self.invoke('build', control.environments()[0]['id'])
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.invoke('build', 'abcdef012346')

    def test_list_search_filters_environments_by_any_value(self):
        other = 'abcdef012346'
        (control.PROJECTS / 'alpha').mkdir()
        control.reconcile(self.settings, {self.identity: control.validate_specification({'subdomain': ['alpha', 'beta']}),
                                          other: control.validate_specification({'php': '8.3'})})
        self.assertEqual(2, len(json.loads(self.invoke('list'))))
        self.assertEqual([self.identity], [item['id'] for item in json.loads(self.invoke('list', '--search', 'ALPHA'))])
        self.assertEqual([self.identity], [item['id'] for item in json.loads(self.invoke('list', '--search', 'beta.example.test'))])
        self.assertEqual([other], [item['id'] for item in json.loads(self.invoke('list', '--search', '8.3'))])
        self.assertEqual([self.identity, other], [item['id'] for item in json.loads(self.invoke('list', '--search', 'abcdef01234'))])
        self.assertEqual([], json.loads(self.invoke('list', '--search', 'missing')))


if __name__ == '__main__':
    unittest.main()
