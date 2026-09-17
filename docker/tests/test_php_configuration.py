import configparser
from pathlib import Path
import unittest


class PhpConfigurationTest(unittest.TestCase):
    def test_inherited_environment_is_available_to_php_applications(self):
        build = (Path(__file__).parents[1] / 'docker-build.sh').read_text()
        configuration = build.split("cat > /etc/php/custom.ini <<'PHPINI'\n", 1)[1].split('\nPHPINI', 1)[0]
        settings = configparser.ConfigParser(interpolation=None)
        settings.read_string('[php]\n' + configuration)
        self.assertIn('E', settings['php'].get('variables_order', 'GPCS'))


if __name__ == '__main__':
    unittest.main()
