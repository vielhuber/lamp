import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


LAMP = Path(__file__).resolve().parents[2] / 'lamp'


class DataRepositoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'bin').mkdir()
        (self.root / 'temporary').mkdir()
        git = self.root / 'bin/git'
        git.write_text('''#!/bin/bash
set -euo pipefail
if [[ "$1" = -C ]]; then
    if [[ "$3" = fetch ]]; then
        count=0
        if [[ -f "$TEST_ROOT/fetch-count" ]]; then read -r count < "$TEST_ROOT/fetch-count"; fi
        count=$((count + 1))
        echo "$count" > "$TEST_ROOT/fetch-count"
        if (( count <= ${TEST_FETCH_FAILURES:-0} )); then
            echo 'ssh: Could not resolve hostname github.com' >&2
            exit 128
        fi
    fi
    if [[ "$3" = reset ]]; then touch "$TEST_ROOT/reset"; fi
    exit 0
fi
target="${@: -1}"
if [[ "$GIT_SSH_COMMAND" = *lamp-data-key* ]]; then
    echo pasted >> "$TEST_ROOT/calls"
    [[ -f "$TEST_ROOT/lamp-data-key" ]] || exit 90
    [[ "${TEST_FAIL_PASTED:-0}" = 0 ]] || exit 1
else
    echo host >> "$TEST_ROOT/calls"
    [[ "$GIT_SSH_COMMAND" = *BatchMode=yes* ]] || exit 91
    [[ "$GIT_SSH_COMMAND" = *test-ssh-config* ]] || exit 92
    [[ "${TEST_FAIL_HOST:-0}" = 0 ]] || exit 1
fi
mkdir -p "$target/.git"
printf 'repository data' > "$target/settings.yaml"
''')
        git.chmod(0o755)
        sleep = self.root / 'bin/sleep'
        sleep.write_text('#!/bin/sh\necho "$1" >> "$TEST_ROOT/sleeps"\n')
        sleep.chmod(0o755)
        source = LAMP.read_text()
        functions = source[source.index('require_docker() {'):source.index('\npreset() {')]
        functions = functions.replace('/var/lib/lamp/data', str(self.root / 'data'))
        functions = functions.replace('/dev/shm/lamp-data-key', str(self.root / 'lamp-data-key'))
        self.script = '''set -euo pipefail
docker() {
    if [[ "$1" = info ]]; then return; fi
    while [[ "$1" != bash ]]; do shift; done
    if [[ "$3" = *'tar -x'* && "${TEST_FAIL_TRANSFER:-0}" != 0 ]]; then
        cat > /dev/null
        return "$TEST_FAIL_TRANSFER"
    fi
    "$@"
}
''' + functions + '''
read_data_key() {
    touch "$TEST_ROOT/prompted"
    data_key='test placeholder'
}
compose=(docker compose)
command=start
data_repository=git@example.test:owner/data.git
sync_data
'''
        self.environment = {**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                            'TEST_ROOT': str(self.root), 'TMPDIR': str(self.root / 'temporary'),
                            'GIT_SSH_COMMAND': 'ssh -F test-ssh-config'}

    def invoke(self, **environment):
        return subprocess.run(['bash', '-c', self.script], env={**self.environment, **environment},
                              capture_output=True, text=True, timeout=10)

    def test_host_access_clones_without_prompt_and_cleans_temporary_checkout(self):
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.root / 'prompted').exists())
        self.assertEqual('host\n', (self.root / 'calls').read_text())
        self.assertEqual('repository data', (self.root / 'data/settings.yaml').read_text())
        self.assertEqual(0, (self.root / 'data/settings.yaml').stat().st_mode & 0o077)
        self.assertEqual([], list((self.root / 'temporary').iterdir()))
        self.assertTrue((self.root / 'data/.git').is_dir())

    def test_failed_host_access_prompts_and_uses_temporary_key(self):
        result = self.invoke(TEST_FAIL_HOST='1')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((self.root / 'prompted').exists())
        self.assertEqual('host\npasted\n', (self.root / 'calls').read_text())
        self.assertFalse((self.root / 'lamp-data-key').exists())
        self.assertEqual([], list((self.root / 'temporary').iterdir()))

    def test_failed_pasted_key_aborts_and_is_removed(self):
        result = self.invoke(TEST_FAIL_HOST='1', TEST_FAIL_PASTED='1')
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / 'lamp-data-key').exists())

    def test_transfer_failure_aborts_without_asking_for_a_key(self):
        for status in ('3', '7'):
            with self.subTest(status=status):
                result = self.invoke(TEST_FAIL_TRANSFER=status)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((self.root / 'prompted').exists())
                self.assertEqual([], list((self.root / 'temporary').iterdir()))

    def test_existing_clone_is_updated_without_prompt_or_host_clone(self):
        (self.root / 'data/.git').mkdir(parents=True)
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.root / 'prompted').exists())
        self.assertFalse((self.root / 'calls').exists())

    def test_add_keeps_json_output_separate_from_data_repository_progress(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    (self.root / 'data/.git').mkdir(parents=True, exist_ok=True)
                script = self.script.replace('command=start', 'command=add')
                script += "printf '%s\\n' '{\"id\":\"abcdef012345\",\"status\":\"ready\"}'\n"
                result = subprocess.run(['bash', '-c', script], env=self.environment,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual({'id': 'abcdef012345', 'status': 'ready'}, json.loads(result.stdout))
                self.assertIn('preparing data repository access', result.stderr)

    def test_dns_failure_is_retried_before_updating_data(self):
        (self.root / 'data/.git').mkdir(parents=True)
        result = self.invoke(TEST_FETCH_FAILURES='2')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('3\n', (self.root / 'fetch-count').read_text())
        self.assertEqual(['5', '5'], (self.root / 'sleeps').read_text().splitlines())
        self.assertTrue((self.root / 'reset').exists())
        self.assertFalse((self.root / 'prompted').exists())

    def test_persistent_dns_failure_aborts_instead_of_using_stale_data(self):
        (self.root / 'data/.git').mkdir(parents=True)
        result = self.invoke(TEST_FETCH_FAILURES='99')
        self.assertNotEqual(0, result.returncode)
        self.assertEqual('5\n', (self.root / 'fetch-count').read_text())
        self.assertEqual(['5'] * 4, (self.root / 'sleeps').read_text().splitlines())
        self.assertFalse((self.root / 'reset').exists())
        self.assertFalse((self.root / 'prompted').exists())
        self.assertNotIn('continuing with the last state', result.stderr)


if __name__ == '__main__':
    unittest.main()
