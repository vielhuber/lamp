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
if [[ "$1" = -c ]]; then
    [[ "$2" = "safe.directory=$TEST_ROOT/data" || "$2" = "safe.directory=$TEST_ROOT/repository" ]] || exit 93
    shift 2
fi
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
    if [[ "$3 $4" = "remote get-url" ]]; then cat "$TEST_ROOT/origin" 2>/dev/null || echo git@example.test:owner/data.git; fi
    if [[ "$3 $4" = "remote set-url" ]]; then echo "$6" > "$TEST_ROOT/origin"; fi
    if [[ "$3 $4" = "sparse-checkout set" ]]; then echo "$6" > "$TEST_ROOT/sparse"; fi
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
echo "$*" >> "$TEST_ROOT/clone-arguments"
mkdir -p "$target/.git" "$target/lamp"
printf 'repository data' > "$target/settings.yaml"
printf 'folder data' > "$target/lamp/settings.yaml"
''')
        git.chmod(0o755)
        sleep = self.root / 'bin/sleep'
        sleep.write_text('#!/bin/sh\necho "$1" >> "$TEST_ROOT/sleeps"\n')
        sleep.chmod(0o755)
        source = LAMP.read_text()
        functions = source[source.index('require_docker() {'):source.index('\npreset() {')]
        functions = functions.replace('/var/lib/lamp/data', str(self.root / 'data'))
        functions = functions.replace('/var/lib/lamp/repository', str(self.root / 'repository'))
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
data_path=${TEST_DATA_PATH:-}
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
                self.assertNotIn('preparing data repository access', result.stdout + result.stderr)

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

    def test_data_path_clones_partially_and_links_the_folder(self):
        result = self.invoke(TEST_DATA_PATH='lamp')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('--filter=blob:none --sparse', (self.root / 'clone-arguments').read_text())
        self.assertTrue((self.root / 'repository/.git').is_dir())
        self.assertEqual('repository/lamp', os.readlink(self.root / 'data'))
        self.assertEqual('folder data', (self.root / 'data/settings.yaml').read_text())
        self.assertEqual('lamp\n', (self.root / 'sparse').read_text())
        self.assertEqual(0, (self.root / 'repository/lamp/settings.yaml').stat().st_mode & 0o077)

    def test_existing_clone_switches_to_a_new_repository_and_data_path_without_cloning(self):
        (self.root / 'data/.git').mkdir(parents=True)
        (self.root / 'data/lamp').mkdir()
        (self.root / 'origin').write_text('git@example.test:owner/old.git\n')
        result = self.invoke(TEST_DATA_PATH='lamp')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.root / 'calls').exists())
        self.assertEqual('git@example.test:owner/data.git\n', (self.root / 'origin').read_text())
        self.assertTrue((self.root / 'repository/.git').is_dir())
        self.assertEqual('repository/lamp', os.readlink(self.root / 'data'))
        self.assertTrue((self.root / 'reset').exists())

    def test_leaving_the_data_path_layout_clones_again(self):
        (self.root / 'repository/.git').mkdir(parents=True)
        (self.root / 'data').symlink_to('repository/lamp')
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('host\n', (self.root / 'calls').read_text())
        self.assertFalse((self.root / 'repository').exists())
        self.assertFalse((self.root / 'data').is_symlink())
        self.assertEqual('repository data', (self.root / 'data/settings.yaml').read_text())

    def test_missing_data_path_in_the_repository_aborts(self):
        (self.root / 'repository/.git').mkdir(parents=True)
        result = self.invoke(TEST_DATA_PATH='missing')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('data_path missing does not exist', result.stderr)
        self.assertFalse((self.root / 'data').exists())


if __name__ == '__main__':
    unittest.main()
