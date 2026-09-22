import unittest
from pathlib import Path

DOCKER = Path(__file__).resolve().parents[1]


class ImageContextTest(unittest.TestCase):
    def test_every_git_hook_is_part_of_the_build_context(self):
        # the ignore file is an allow list: a hook that is missing there never reaches the image
        allowed = {line[1:] for line in (DOCKER / 'Dockerfile.dockerignore').read_text().splitlines() if line.startswith('!')}
        hooks = sorted(path.name for path in (DOCKER / 'git-hooks').iterdir())
        self.assertTrue(hooks)
        self.assertEqual([], [hook for hook in hooks if 'docker/git-hooks/' + hook not in allowed])

    def test_every_runtime_script_is_part_of_the_build_context(self):
        allowed = {line[1:] for line in (DOCKER / 'Dockerfile.dockerignore').read_text().splitlines() if line.startswith('!')}
        scripts = [path.name for path in (DOCKER / 'scripts').iterdir() if path.is_file()]
        self.assertEqual([], [script for script in scripts if 'docker/scripts/' + script not in allowed])


if __name__ == '__main__':
    unittest.main()
