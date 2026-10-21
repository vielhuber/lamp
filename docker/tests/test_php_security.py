import os
from pathlib import Path
import shlex
import subprocess
import unittest


PATCHED_VERSIONS = {
    '5.6': '5.6.40-104', '7.0': '7.0.33-92', '7.1': '7.1.33-80',
    '7.2': '7.2.34-66', '7.3': '7.3.33-35', '7.4': '1:7.4.33-31',
    '8.0': '1:8.0.30-25', '8.1': '8.1.34-9', '8.2': '8.2.35-1',
    '8.3': '8.3.36-1', '8.4': '8.4.27-1', '8.5': '8.5.12-1',
}
UNPATCHED_VERSIONS = {
    '5.6': '5.6.40-103', '7.0': '7.0.33-91', '7.1': '7.1.33-79',
    '7.2': '7.2.34-65', '7.3': '7.3.33-34', '7.4': '1:7.4.33-30',
    '8.0': '1:8.0.30-24', '8.1': '8.1.34-8', '8.2': '8.2.34-99',
    '8.3': '8.3.35-99', '8.4': '8.4.26-99', '8.5': '8.5.11-99',
}


class PhpSecurityTest(unittest.TestCase):
    def invoke(self, package='', version=''):
        build = (Path(__file__).parents[1] / 'docker-build.sh').read_text()
        versions = build.split('php_versions=(', 1)[1].split('\n', 1)[0]
        minimums = build.split('declare -A php_security_minimum_versions=(', 1)[1].split('\n)', 1)[0]
        installation = build.split("step 'php extensions'\n", 1)[1].split('\n# PHP 8.5 includes OPcache', 1)[0]
        fixtures = ' '.join(f'[{key}]={shlex.quote(value)}' for key, value in PATCHED_VERSIONS.items())
        script = f'''set -euo pipefail
declare -A test_versions=({fixtures})
apt-get() {{ :; }}
dpkg-query() {{
    if [[ "$3" = "$TEST_PACKAGE" ]]; then
        [[ -n "$TEST_VERSION" ]] || return 1
        printf '%s' "$TEST_VERSION"
        return
    fi
    local branch=${{3#php}}
    branch=${{branch%%-*}}
    printf '%s' "${{test_versions[$branch]}}"
}}
php_versions=({versions}
declare -A php_security_minimum_versions=({minimums}
)
{installation}
'''
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10,
                              env={**os.environ, 'TEST_PACKAGE': package, 'TEST_VERSION': version})

    def test_patched_packages_pass_for_every_installed_branch(self):
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unpatched_packages_fail_even_if_other_packages_are_patched(self):
        for branch, version in UNPATCHED_VERSIONS.items():
            for component in ('common', 'cli', 'fpm'):
                package = f'php{branch}-{component}'
                with self.subTest(package=package):
                    result = self.invoke(package, version + '+0~20261021.1+ubuntu26.04~1')
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(package, result.stderr)
                    self.assertIn('CVE-2026-109618', result.stderr)
                    self.assertIn('CVE-2026-109619', result.stderr)

    def test_newer_and_distribution_package_versions_are_accepted(self):
        for version in ('8.5.12-1+0~20261021.27+ubuntu26.04~1', '8.5.13-1'):
            with self.subTest(version=version):
                result = self.invoke('php8.5-fpm', version)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_missing_package_fails(self):
        result = self.invoke('php8.5-fpm')
        self.assertNotEqual(0, result.returncode)


if __name__ == '__main__':
    unittest.main()
