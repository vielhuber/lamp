import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
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
        for name in ('CONFIGURATION', 'SETUP', 'STATE', 'PROJECTS', 'SITES', 'ENABLED'):
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
        (control.SETUP / 'setup.yaml').write_text('domain: example.test\n')
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

    def test_unknown_subdomain_is_reported_as_one_line(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            self.invoke('show', 'nowhere')
        self.assertEqual(2, raised.exception.code)
        self.assertEqual('❌ No environment has the subdomain nowhere; see ./lamp help\n', errors.getvalue())

    def test_normal_repository_clone_keeps_history_and_streams_progress(self):
        self.invoke('add', '--git', 'git@example.test:owner/project.git', '--branch', 'main', '--subdomain', 'project')
        clone = next(call for call in self.run.call_args_list if call.args[0][:2] == ['git', 'clone'])
        self.assertIn('--progress', clone.args[0])
        self.assertNotIn('--depth', clone.args[0])
        self.assertIs(control.sys.stderr, clone.kwargs.get('output'))

    def test_phpmyadmin_clone_only_downloads_the_selected_branch_tip(self):
        for number, repository in enumerate(('https://github.com/phpmyadmin/phpmyadmin.git',
                                             'git@github.com:phpmyadmin/phpmyadmin.git')):
            with self.subTest(repository=repository):
                self.run.reset_mock()
                self.invoke('add', '--git', repository, '--branch', 'STABLE', '--subdomain', f'phpmyadmin-{number}')
                clone = next(call for call in self.run.call_args_list if call.args[0][:2] == ['git', 'clone'])
                arguments = clone.args[0]
                self.assertIn('--progress', arguments)
                self.assertIn('--depth', arguments)
                self.assertEqual('1', arguments[arguments.index('--depth') + 1])
                self.assertIn('--single-branch', arguments)
                self.assertIn('--no-tags', arguments)
                self.assertEqual('STABLE', arguments[arguments.index('--branch') + 1])
                self.assertIs(control.sys.stderr, clone.kwargs.get('output'))

    def test_existing_directory_is_adopted_without_build_until_build_is_requested(self):
        project = control.PROJECTS / 'existing'
        project.mkdir()
        original = project / 'original.txt'
        original.write_text('keep')
        build = 'test "$DB_PASSWORD" = rootpw && test "$DB_DATABASE" = existing && test "$DB_USERNAME" = root && printf built > build-result.txt'
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

    def test_new_static_environment_and_failed_build_only_build_on_explicit_request(self):
        project = control.PROJECTS / 'manual'
        self.invoke('add', '--id', self.identity, '--subdomain', 'manual', '--build', 'printf built > build-result.txt')
        self.assertFalse((project / 'build-result.txt').exists())
        self.assertEqual('ready', control.load_environment(self.identity)['status'])
        self.assertIsNone(control.load_environment(self.identity)['build_hash'])
        self.vhost.assert_called()
        self.invoke('build', 'manual')
        self.assertEqual('built', (project / 'build-result.txt').read_text())
        value = control.validate_specification({'subdomain': 'manual', 'build': 'exit 7'})
        _, original = control.read_desired()
        control.write_desired([value], original)
        control.reconcile(self.settings, {self.identity: value})
        self.assertEqual('built', (project / 'build-result.txt').read_text())
        with self.assertRaisesRegex(RuntimeError, 'bash failed'):
            self.invoke('build', 'manual')
        self.assertEqual('failed', control.load_environment(self.identity)['status'])
        control.reconcile(self.settings, {self.identity: value})
        self.assertEqual('ready', control.load_environment(self.identity)['status'])
        self.assertEqual('built', (project / 'build-result.txt').read_text())

    def test_shared_script_changes_do_not_rebuild_or_reconfigure_static_environments(self):
        remote = 'git@example.test:owner/project.git'
        script = control.build_script(remote)
        script.parent.mkdir()
        script.write_text('printf automatic > build-result.txt\n')
        value = control.validate_specification({'git': remote, 'subdomain': 'manual'})
        control.reconcile(self.settings, {self.identity: value})
        project = control.PROJECTS / 'manual'
        self.assertFalse((project / 'build-result.txt').exists())
        self.vhost.reset_mock()
        script.write_text('exit 1\n')
        with patch.object(control, 'resolve_build', side_effect=AssertionError('Static startup must not load a build script')):
            control.reconcile(self.settings, {self.identity: value})
        self.vhost.assert_not_called()
        self.assertFalse((project / 'build-result.txt').exists())

    def test_new_dynamic_environment_still_builds_automatically(self):
        self.invoke('add', '--id', self.identity, '--build', 'printf dynamic > build-result.txt')
        project = control.PROJECTS / '_environments' / self.identity
        self.assertEqual('dynamic', (project / 'build-result.txt').read_text())

    def test_yaml_add_uses_repository_script_without_changing_existing_git_checkout(self):
        project = control.PROJECTS / 'existing'
        project.mkdir()
        remote = 'git@example.test:owner/project.git'
        script = control.build_script(remote)
        script.parent.mkdir()
        script.write_text('printf built > build-result.txt\n')
        value = control.validate_specification({'git': remote, 'subdomain': 'existing'})
        control.write_desired([value], None)
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
        remote = 'git@example.test:owner/project.git'
        script = control.build_script(remote)
        script.parent.mkdir()
        script.write_text('exit 1\n')
        self.invoke('add', '--id', self.identity, '--git', remote, '--subdomain', 'created')
        with self.assertRaisesRegex(RuntimeError, 'bash failed'):
            self.invoke('build', 'created')
        self.assertEqual('failed', control.load_environment(self.identity)['status'])
        script.write_text('printf recovered > build-result.txt\n')
        self.run.reset_mock()
        self.invoke('build', 'created')
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
                control.reconcile(self.settings, {self.identity: value})
                if not adopted:
                    control.write_desired([value], None)
                    self.invoke('build', label)
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
        self.assertEqual('', self.invoke('build', identity))
        environment = control.load_environment(identity)
        self.assertEqual('built', (project / 'build-result.txt').read_text())
        self.assertEqual('ready', environment['status'])
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
        self.assertIn('export PATH=' + str(control.STATE / 'environments' / environment['id'] / 'bin') + ':"$PATH"\n', setup)
        self.assertNotIn('export PATH=/', setup.replace('export PATH=' + str(control.STATE), ''))
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
            with self.subTest(arguments=arguments), self.assertRaises((ValueError, SystemExit)):
                self.invoke('add', *arguments)
        dynamic = json.loads(self.invoke('add'))
        environment = [item for item in control.environments() if item['id'] == dynamic['id']][0]
        (control.CONFIGURATION / 'syncdb' / 'blog-production-local.json').write_text(json.dumps({'engine': 'mysql', 'source': {}, 'replace': {
            'https://www.blog.com': 'https://blog.other.test', '@blog.other.test': '@www.blog.com', 'shop.blog.com': 'blog-shop.other.test',
            'smtp.blog.com': 'sslout.provider.test', 'wp-fastest-cache': 'wpfastestcache.off', 'blog.sub.production.com': 'blog.other.test'}}))
        with patch.object(control, 'run_sync') as sync:
            control.sync_database(environment, 'blog-production-local')
        hostname = dynamic['id'] + '.example.test'
        self.assertEqual({'https://www.blog.com': 'https://' + hostname, '@' + hostname: '@www.blog.com', 'shop.blog.com': dynamic['id'] + '-shop.example.test',
                          'smtp.blog.com': 'sslout.provider.test', 'wp-fastest-cache': 'wpfastestcache.off', 'blog.sub.production.com': hostname},
                         sync.call_args.args[1]['replace'])
        self.assertEqual('lamp_' + dynamic['id'], sync.call_args.args[1]['target']['database'])
        self.assertEqual(360, sync.call_args.args[1]['cache'])
        self.assertEqual(min(16, os.cpu_count() or 1), sync.call_args.args[1]['threads'])
        (control.CONFIGURATION / 'syncdb' / 'fresh-production-local.json').write_text(json.dumps({'engine': 'mysql', 'source': {}, 'cache': 0, 'threads': 1}))
        with patch.object(control, 'run_sync') as sync:
            control.sync_database(environment, 'fresh-production-local')
        self.assertEqual((0, 1), (sync.call_args.args[1]['cache'], sync.call_args.args[1]['threads']))
        (control.PROJECTS / 'plain').mkdir()
        self.invoke('add', '--subdomain', 'plain')
        plain = [item for item in control.environments() if item['subdomain'] == 'plain'][0]
        self.assertNotIn('DB_', Path(plain['setup_environment']).read_text())
        self.assertIsNone(plain['database'])

    def test_a_build_switches_a_dynamic_environment_to_postgres(self):
        dynamic = json.loads(self.invoke('add'))
        with patch.dict(os.environ, {'LAMP_ID': dynamic['id']}):
            self.assertEqual('export DB_CONNECTION=pgsql\nexport DB_PORT=5432\n', self.invoke('db_engine', 'postgres'))
        setup = Path(control.load_environment(dynamic['id'])['setup_environment']).read_text()
        self.assertIn('export DB_CONNECTION=pgsql\n', setup)
        self.assertIn('export DB_PORT=5432\n', setup)
        self.assertIn('export DB_DATABASE=lamp_' + dynamic['id'] + '\n', setup)
        self.assertIn('db_engine() {', setup)
        (control.PROJECTS / 'shop').mkdir()
        static = json.loads(self.invoke('add', '--subdomain', 'shop', '--db-name', 'shop', '--db-engine', 'mysql'))
        with patch.dict(os.environ, {'LAMP_ID': static['id']}):
            self.assertEqual('', self.invoke('db_engine', 'mysql'))
            with self.assertRaisesRegex(ValueError, 'env.yaml sets db_engine mysql'):
                self.invoke('db_engine', 'postgres')

    def test_reset_removes_only_the_dynamic_environments(self):
        (control.PROJECTS / 'shop').mkdir()
        self.invoke('add', '--subdomain', 'shop')
        first, second = json.loads(self.invoke('add'))['id'], json.loads(self.invoke('add'))['id']
        output = self.invoke('reset')
        self.assertIn('2 dynamic environment(s) removed', output)
        self.assertEqual(['shop'], [item['subdomain'] for item in control.environments()])
        self.assertTrue((control.PROJECTS / 'shop').is_dir())
        for identity in (first, second):
            self.assertFalse((control.PROJECTS / '_environments' / identity).exists())
        self.assertIn('0 dynamic environment(s) removed', self.invoke('reset'))

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
        self.assertEqual('✅ shop-production-local imported\n', self.invoke('sync', 'shop-production-local'))
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

    def test_build_output_is_written_to_a_private_log_named_in_the_failure(self):
        project = control.PROJECTS / 'logged'
        value = control.validate_specification({'subdomain': 'logged', 'build': 'echo progress; echo problem >&2; exit 3'})
        self.invoke('add', '--id', self.identity, '--subdomain', 'logged', '--build', value['build'])
        with patch.object(control, 'print', create=True) as messages, \
             self.assertRaisesRegex(RuntimeError, 'Build log: .*/environments/abcdef012345/build.log') as raised:
            self.invoke('build', 'logged')
        self.assertIn('logged.example.test (abcdef012345): bash failed (exit 3)', str(raised.exception))
        messages.assert_any_call('❌ logged.example.test (abcdef012345) failed; fix the reported error and retry',
                                 file=control.sys.stderr)
        log = control.STATE / 'environments' / self.identity / 'build.log'
        self.assertIn('+ echo progress\nprogress\n', log.read_text())
        self.assertIn('problem\n', log.read_text())
        self.assertEqual(0o600, log.stat().st_mode & 0o777)
        value['build'] = 'echo fixed'
        _, original = control.read_desired()
        control.write_desired([value], original)
        control.reconcile(self.settings, {self.identity: value})
        self.invoke('build', 'logged')
        self.assertIn('fixed\n', log.read_text())
        self.assertEqual('ready', control.load_environment(self.identity)['status'])

    def test_build_command_requires_configured_build_and_known_environment(self):
        control.write_desired([control.validate_specification({'subdomain': 'plain'})], None)
        (control.PROJECTS / 'plain').mkdir()
        control.reconcile(self.settings, control.desired_state(control.read_desired()[0]))
        with self.assertRaisesRegex(ValueError, 'has no build'):
            self.invoke('build', control.environments()[0]['id'])
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.invoke('build', 'abcdef012346')

    def test_build_by_directory_uses_registered_environment_from_root_or_subfolder(self):
        project = control.PROJECTS / 'project-folder'
        (project / 'folder with spaces').mkdir(parents=True)
        self.invoke('add', '--subdomain', 'different-name', '--directory', project.name,
                    '--build', 'printf built >> build-result.txt')
        self.invoke('build', '--directory', str(project))
        self.invoke('build', '--directory', str(project / 'folder with spaces'))
        self.assertEqual('builtbuilt', (project / 'build-result.txt').read_text())

    def test_build_by_unregistered_directory_does_not_build_other_projects(self):
        project = control.PROJECTS / 'project'
        project.mkdir()
        self.invoke('add', '--subdomain', 'project', '--build', 'touch build-result.txt')
        with self.assertRaisesRegex(ValueError, 'No environment.*directory'):
            self.invoke('build', '--directory', str(control.PROJECTS / 'project-other'))
        self.assertFalse((project / 'build-result.txt').exists())

    def test_build_by_directory_selects_closest_project_root(self):
        project = control.PROJECTS / 'parent'
        child = project / 'child'
        child.mkdir(parents=True)
        self.invoke('add', '--subdomain', 'parent', '--build', 'touch build-result.txt')
        self.invoke('add', '--subdomain', 'child', '--directory', 'parent/child',
                    '--build', 'touch build-result.txt')
        self.invoke('build', '--directory', str(child))
        self.assertTrue((child / 'build-result.txt').exists())
        self.assertFalse((project / 'build-result.txt').exists())

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
