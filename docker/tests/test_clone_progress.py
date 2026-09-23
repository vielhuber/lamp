from pathlib import Path
import subprocess
import unittest


LAMP = Path(__file__).resolve().parents[2] / 'lamp'


class CloneProgressTest(unittest.TestCase):
    def test_start_and_restart_keep_git_progress_in_the_terminal(self):
        source = LAMP.read_text()
        function = source[source.index('    terminal_lines() {'):source.index('\n    exec > >(')]
        progress = ('📥 cloning repository\n'
                    'remote: Enumerating objects: 10, done.\n'
                    'remote: Counting objects: 100% (10/10), done.\n'
                    'remote: Compressing objects: 100% (5/5), done.\n'
                    'Receiving objects: 100% (10/10), 20 KiB | 1 MiB/s, done.\n'
                    'Resolving deltas: 100% (2/2), done.\n'
                    'Updating files: 100% (4/4), done.\n')
        for command in ('start', 'restart'):
            with self.subTest(command=command):
                result = subprocess.run(['bash', '-c', f'command={command}\n' + function + '\nterminal_lines'],
                                        input=progress + 'unrelated command output\n', text=True, capture_output=True, check=True)
                self.assertEqual(progress, result.stdout)


if __name__ == '__main__':
    unittest.main()
