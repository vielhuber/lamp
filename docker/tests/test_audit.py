import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class AuditTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        self.config = self.root / 'env.yaml'
        self.config.write_text('[]\n')
        (self.root / 'bin').mkdir()
        source = (ROOT / 'docker/scripts/audit.sh').read_text()
        self.script = source.replace('/var/www', str(self.projects)).replace('/etc/lamp-config/env.yaml', str(self.config))
        self.environment = {**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                            'TEST_ROOT': str(self.root), 'TEST_REPOSITORIES': '', 'TEST_ORGANIZATIONS': '',
                            'TEST_ORGANIZATION_REPOSITORIES': '', 'TEST_DIRTY': '',
                            'TEST_BEHIND': '0', 'TEST_DIVERGED': '0', 'TEST_FETCH': '0'}
        self.executable('gh', '''
if [[ "$1 $2" = 'org list' ]]; then
    printf '%s\\n' "$TEST_ORGANIZATIONS"
elif [[ "$3" = vielhuber ]]; then
    printf '%s\\n' "$TEST_REPOSITORIES"
else
    printf '%s\\n' "$TEST_ORGANIZATION_REPOSITORIES"
fi
''')
        self.executable('git', '''
project=$2
shift 2
printf '%s %s\\n' "$(basename "$project")" "$1" >> "$TEST_ROOT/git-calls"
case "$1" in
    remote) cat "$project/.origin" 2>/dev/null || exit 2 ;;
    rev-parse) exit 0 ;;
    status) printf '%s' "$TEST_DIRTY" ;;
    fetch) exit "$TEST_FETCH" ;;
    rev-list)
        if [[ "$*" = *--left-only* ]]; then printf '%s' "$TEST_DIVERGED"
        elif [[ -f "$TEST_ROOT/pulled" ]]; then printf 0
        else printf '%s' "$TEST_BEHIND"; fi ;;
    pull)
        [[ "$*" = 'pull --ff-only --no-rebase --no-autostash' ]] || exit 90
        [[ "$TEST_DIVERGED" = 0 ]] || exit 1
        touch "$TEST_ROOT/pulled" ;;
esac
''')

    def executable(self, name, body):
        path = self.root / 'bin' / name
        path.write_text('#!/bin/bash\nset -eu\n' + body + '\n')
        path.chmod(0o755)

    def project(self, name, registered=True):
        project = self.projects / name
        project.mkdir()
        (project / '.git').write_text('gitdir: fixture\n')
        if registered:
            entries = json.loads(self.config.read_text())
            entries.append({'subdomain': name, 'directory': str(project)})
            self.config.write_text(json.dumps(entries))
        return project

    def invoke(self, **environment):
        result = subprocess.run(['bash', '-c', self.script], env={**self.environment, **environment},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def test_empty_sections_and_static_heading_are_hidden(self):
        output = self.invoke()
        self.assertNotIn('🔎', output)
        self.assertNotIn('Missing environments are included', output)
        self.assertIn('Analyzing 0 projects', output)

    def test_mapping_exclusions_worktrees_hidden_folders_and_missing_lamp(self):
        (self.project('vielhuber') / '.origin').write_text('git@github.com:vielhuber/vielhuber.de.git')
        self.project('.hidden', registered=False)
        self.project('_archive', registered=False)
        (self.projects / '.plain').mkdir()
        output = self.invoke(TEST_REPOSITORIES='setup url\nvielhuber url\nvielhuber.de git@github.com:vielhuber/vielhuber.de.git\nforgotten git@github.com:vielhuber/forgotten.git')
        self.assertIn('forgotten [not cloned]', output)
        self.assertIn('1 repositories missing locally.', output)
        self.assertIn('.plain [no git]', output)
        self.assertIn('1 folders without a Git repository.', output)
        self.assertIn('Analyzing 2 projects', output)
        self.assertRegex(output, r'\.hidden\s+\[lamp missing\]')
        self.assertIn('1 projects are up to date.', output)

    def test_clean_behind_repository_is_pulled_before_dependency_checks(self):
        project = self.project('clean')
        (project / 'package.json').write_text('{}')
        self.executable('ncu', 'if [[ -f "$TEST_ROOT/pulled" ]]; then touch "$TEST_ROOT/pulled-before-npm"; fi; printf "{}"')
        output = self.invoke(TEST_BEHIND='1')
        self.assertTrue((self.root / 'pulled').exists())
        self.assertTrue((self.root / 'pulled-before-npm').exists())
        self.assertNotIn('behind/ahead', output)
        self.assertIn('1 projects are up to date.', output)

    def test_foreign_repositories_are_excluded_before_fetch_and_project_checks(self):
        for name, remote in [('phpmyadmin', 'https://github.com/phpmyadmin/phpmyadmin.git'),
                             ('foreign-ssh', 'git@github.com:another-owner/project.git'),
                             ('foreign-uri', 'ssh://git@github.com/another-owner/project.git')]:
            project = self.project(name, registered=False)
            (project / '.origin').write_text(remote)
        output = self.invoke(TEST_BEHIND='1')
        self.assertIn('Analyzing 0 projects', output)
        self.assertNotIn('lamp missing', output)
        self.assertNotIn('no git', output)
        calls = (self.root / 'git-calls').read_text()
        self.assertNotIn('fetch', calls)
        self.assertNotIn('status', calls)
        self.assertFalse((self.root / 'pulled').exists())

    def test_own_public_private_and_unknown_origins_remain_in_audit(self):
        for name, remote in [('private', 'git@github.com:vielhuber/private.git'),
                             ('public', 'https://github.com/vielhuber/public.git'),
                             ('ssh-uri', 'ssh://git@github.com/Vielhuber/project.git'),
                             ('self-hosted', 'git@example.test:private/project.git')]:
            project = self.project(name, registered=False)
            (project / '.origin').write_text(remote)
        self.project('no-origin', registered=False)
        output = self.invoke()
        self.assertIn('Analyzing 5 projects', output)
        self.assertEqual(5, output.count('[lamp missing]'))

    def test_organization_repositories_are_listed_and_checked(self):
        project = self.project('local-project', registered=False)
        (project / '.origin').write_text('git@github.com:Example-Org/project.git')
        output = self.invoke(TEST_ORGANIZATIONS='example-org',
                             TEST_ORGANIZATION_REPOSITORIES='project git@github.com:example-org/project.git\nmissing git@github.com:example-org/missing.git')
        self.assertIn('missing [not cloned]', output)
        self.assertIn('1 repositories missing locally.', output)
        self.assertIn('Analyzing 1 projects', output)
        self.assertRegex(output, r'local-project\s+\[lamp missing\]')
        self.assertIn('local-project fetch', (self.root / 'git-calls').read_text())

    def test_renamed_repositories_are_matched_by_origin_across_github_url_formats(self):
        repositories = []
        for index, remote in enumerate(['git@github.com:example-org/project-0.git',
                                        'https://github.com/Example-Org/project-1',
                                        'ssh://git@github.com/example-org/project-2.git',
                                        'https://github.com/example-org/project-3.git/']):
            project = self.project(f'local-folder-{index}')
            (project / '.origin').write_text(remote)
            repositories.append(f'project-{index} git@github.com:example-org/project-{index}.git')
        output = self.invoke(TEST_ORGANIZATIONS='example-org',
                             TEST_ORGANIZATION_REPOSITORIES='\n'.join(repositories))
        self.assertNotIn('not cloned', output)
        self.assertIn('4 projects are up to date.', output)

    def test_matching_folder_names_do_not_hide_missing_origins_or_different_owners(self):
        project = self.project('project')
        (project / '.origin').write_text('git@github.com:another-owner/project.git')
        self.project('without-origin')
        output = self.invoke(TEST_REPOSITORIES='project git@github.com:vielhuber/project.git\nwithout-origin git@github.com:vielhuber/without-origin.git')
        self.assertIn('project [not cloned]', output)
        self.assertIn('without-origin [not cloned]', output)
        self.assertIn('2 repositories missing locally.', output)

    def test_missing_repository_check_excludes_archived_personal_and_organization_repositories(self):
        self.executable('gh', '''
if [[ "$1 $2" = 'org list' ]]; then
    printf 'example-org\\n'
else
    printf '%s-active url\\n' "$3"
    if [[ " $* " != *' --no-archived '* ]]; then printf '%s-archived url\\n' "$3"; fi
fi
''')
        output = self.invoke()
        self.assertIn('vielhuber-active [not cloned]', output)
        self.assertIn('example-org-active [not cloned]', output)
        self.assertNotIn('archived', output)
        self.assertIn('2 repositories missing locally.', output)

    def test_failed_organization_repository_lookup_remains_visible(self):
        self.executable('gh', '''
if [[ "$1 $2" = 'org list' ]]; then printf 'example-org\\n';
elif [[ "$3" = example-org ]]; then exit 1;
fi
''')
        self.assertIn('GitHub repository check failed', self.invoke())

    def test_dynamic_environment_directory_is_not_a_missing_repository(self):
        self.project('project')
        (self.projects / '_environments' / 'dynamic-project').mkdir(parents=True)
        output = self.invoke()
        self.assertNotIn('_environments', output)
        self.assertNotIn('Folders without a Git repository', output)
        self.assertIn('Analyzing 1 projects...', output)
        self.assertIn('1 projects are up to date.', output)

    def test_dirty_and_failed_fetch_repositories_are_never_pulled(self):
        self.project('project')
        for changes in (' M tracked', 'M  staged', '?? untracked'):
            with self.subTest(changes=changes):
                output = self.invoke(TEST_BEHIND='1', TEST_DIRTY=changes)
                self.assertIn('modified, behind/ahead', output)
                self.assertFalse((self.root / 'pulled').exists())
        for status, warning in [('1', 'fetch failed'), ('124', 'fetch timeout')]:
            output = self.invoke(TEST_BEHIND='1', TEST_FETCH=status)
            self.assertIn(warning, output)
            self.assertFalse((self.root / 'pulled').exists())

    def test_diverged_repository_stays_flagged_without_merging(self):
        self.project('project')
        output = self.invoke(TEST_BEHIND='1', TEST_DIVERGED='1')
        self.assertIn('behind/ahead, pull failed', output)
        self.assertFalse((self.root / 'pulled').exists())

    def test_configuration_and_github_failures_remain_visible(self):
        self.project('project')
        self.config.write_text('{}')
        self.executable('gh', 'exit 1')
        output = self.invoke()
        self.assertIn('GitHub repository check failed', output)
        self.assertIn('LAMP environment configuration could not be read', output)
        self.assertIn('lamp check failed', output)
        self.assertIn('0 projects are up to date.', output)

    def test_dependency_checks_and_wordpress_runtime_files(self):
        project = self.project('project')
        theme = project / 'wp-content/themes/project'
        theme.mkdir(parents=True)
        for name, value in [('package.json', '{"devDependencies":{"dependency":"^1.1.0"}}'), ('composer.json', '{}'), ('.phprc', '8.4'), ('.nvmrc', '20')]:
            (theme / name).write_text(value)
        ignored = project / 'node_modules/dependency'
        ignored.mkdir(parents=True)
        (ignored / 'package.json').write_text('{}')
        self.executable('ncu', '''printf '%s\\n' "$PWD" >> "$TEST_ROOT/npm-calls"
printf '{"dependency":"^2.0.0"}'
''')
        self.executable('composer', 'exit 99')
        self.executable('php8.5', 'exit 1')
        output = self.invoke()
        self.assertIn('npm, composer, php, nvm', output)
        self.assertEqual([str(theme)], (self.root / 'npm-calls').read_text().splitlines())

    def test_vue_tutorial_skips_only_npm_checks(self):
        project = self.project('vuejs-tutorial', registered=False)
        lesson = project / '04 - cli/my-project'
        lesson.mkdir(parents=True)
        (lesson / 'package.json').write_text('{"dependencies":{"vue":"^2.0.0"}}')
        (lesson / 'composer.json').write_text('{}')
        self.executable('ncu', 'touch "$TEST_ROOT/ncu-called"; printf \'{"vue":"^3.0.0"}\'')
        self.executable('composer', 'exit 1')
        self.executable('php8.5', 'exit 1')
        output = self.invoke(TEST_DIRTY=' M tracked', TEST_BEHIND='1')
        self.assertIn('[lamp missing, composer, modified, behind/ahead]', output)
        self.assertFalse((self.root / 'ncu-called').exists())

    def test_uninstalled_dev_dependencies_are_checked_without_upgrading(self):
        project = self.project('boilerplate')
        manifest = '{"devDependencies":{"critical":"^8.0.0"}}'
        (project / 'package.json').write_text(manifest)
        self.executable('ncu', '''
[[ "$*" = *--no-upgrade* && "$*" = *'--install never'* && "$*" = *'--timeout 60000'* ]] || exit 2
printf '{"critical":"^9.0.0"}'
''')
        output = self.invoke()
        self.assertRegex(output, r'boilerplate\s+\[npm\]')
        self.assertEqual(manifest, (project / 'package.json').read_text())
        self.assertFalse((project / 'node_modules').exists())

    def test_minor_and_patch_updates_do_not_trigger_npm_warning(self):
        project = self.project('project')
        (project / 'package.json').write_text('{"dependencies":{"minor":"^2.0.0","patch":"~1.0.0"}}')
        self.executable('ncu', 'printf \'{"minor":"^2.1.0","patch":"~1.0.1"}\'')
        self.assertIn('1 projects are up to date.', self.invoke())

    def test_failed_npm_check_is_not_reported_as_up_to_date(self):
        project = self.project('project')
        (project / 'package.json').write_text('{}')
        self.executable('ncu', 'exit 1')
        output = self.invoke()
        self.assertIn('[npm check failed]', output)
        self.assertIn('0 projects are up to date.', output)

    def test_host_dispatches_audit_inside_container_without_builds(self):
        (self.root / 'lamp').write_text((ROOT / 'lamp').read_text())
        (self.root / '.config').mkdir()
        (self.root / '.config/setup.yaml').write_text('domain: example.test\n')
        (self.root / 'docker').mkdir()
        (self.root / 'docker/docker-compose.override.yml').write_text('services: {}\n')
        self.executable('docker', 'printf "%s\\n" "$*" >> "$TEST_ROOT/docker-calls"\nif [[ "$1" = image ]]; then exit 1; fi')
        result = subprocess.run(['bash', str(self.root / 'lamp'), 'audit'], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        calls = (self.root / 'docker-calls').read_text().splitlines()
        self.assertEqual(4, len(calls))
        self.assertEqual(['info', 'image inspect ghcr.io/vielhuber/lamp:latest', 'info'], calls[:3])
        self.assertTrue(calls[3].endswith('exec -T app bash /opt/lamp/audit.sh'))
        self.assertNotIn('build', calls[3])


if __name__ == '__main__':
    unittest.main()
