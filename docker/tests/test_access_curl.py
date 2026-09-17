import importlib.util
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

spec = importlib.util.spec_from_file_location('lamp_curl', Path(__file__).parents[1] / 'scripts/curl.py')
curl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curl)


class AccessCurlTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.headers = []
        def read(path):
            if path.name == 'environment.json':
                return json.dumps({'url': 'https://example.invalid'})
            return yaml.safe_dump({'CF-Access-Client-Id': 'test-id', 'CF-Access-Client-Secret': 'test-secret'})
        def run(arguments, **options):
            self.calls.append(arguments)
            if options.get('pass_fds'):
                self.headers.append(os.read(options['pass_fds'][0], 4096).decode())
            return SimpleNamespace(returncode=0)
        for mocked in [patch.object(Path, 'read_text', read), patch.object(curl.subprocess, 'run', run)]:
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_tokens_are_in_memory_not_arguments_and_redirects_are_disabled(self):
        self.assertEqual(0, curl.main(['abcdef123456', '--', '-fsSL', '--output', '/tmp/test-output', 'https://example.invalid/file']))
        self.assertNotIn('test-secret', ' '.join(self.calls[0]))
        self.assertIn('CF-Access-Client-Secret: test-secret', self.headers[0])
        self.assertEqual('0', self.calls[0][self.calls[0].index('--max-redirs') + 1])
        self.assertEqual('--disable', self.calls[0][1])

    def test_foreign_origins_never_receive_credentials(self):
        for url in ['https://example.invalid.evil.test/', 'http://example.invalid', 'https://example.invalid:8443', 'https://other.invalid']:
            with self.subTest(url=url):
                curl.main(['--automatic', 'abcdef123456', '-sS', url])
                self.assertNotIn('--header', self.calls[-1])
                self.assertEqual([], self.headers)
                with self.assertRaises(ValueError):
                    curl.main(['abcdef123456', url])

    def test_extra_urls_and_secret_exposing_options_are_refused(self):
        for extra in [['evil.invalid'], ['https://evil.invalid'], ['--url', 'https://evil.invalid'], ['-v'], ['--trace', '/tmp/trace'], ['--config', '/tmp/config'], ['--next'], ['--connect-to', 'example.invalid:443:evil.invalid:443'], ['--write-out', '%{json}']]:
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    curl.main(['abcdef123456', 'https://example.invalid', *extra])
        self.assertEqual([], self.calls)

    def test_foreign_requests_and_version_checks_keep_native_curl_options(self):
        for arguments in [['--version'], ['--retry', '3', '--insecure', 'https://other.invalid']]:
            curl.main(['--automatic', 'abcdef123456', *arguments])
            self.assertEqual(['/usr/bin/curl', *arguments], self.calls[-1])
        self.assertEqual([], self.headers)

    def test_authenticated_requests_cannot_disable_tls_verification(self):
        for option in ['-k', '-ksSf', '--insecure']:
            with self.assertRaises(ValueError):
                curl.main(['--automatic', 'abcdef123456', option, 'https://example.invalid'])
        self.assertEqual([], self.calls)

    def test_option_values_are_not_mistaken_for_destination_urls(self):
        curl.main(['abcdef123456', '-X', 'POST', '--data-raw', 'https://other.invalid', 'https://example.invalid'])
        self.assertEqual(1, len(self.headers))

    def test_public_requests_do_not_read_or_send_service_tokens(self):
        def read(path):
            if path.name != 'environment.json':
                raise AssertionError('Service token must not be read for public requests')
            return json.dumps({'url': 'https://example.invalid', 'visibility': 'public'})
        with patch.object(Path, 'read_text', read):
            self.assertEqual(0, curl.main(['abcdef123456', 'https://example.invalid']))
        self.assertEqual([], self.headers)
        self.assertNotIn('--header', self.calls[0])

    def test_aliases_receive_credentials_only_at_exact_https_origins(self):
        read = Path.read_text
        def metadata(path):
            if path.name == 'environment.json':
                return json.dumps({'url': 'https://example.invalid',
                                   'hostnames': ['example.invalid', 'example-shop.invalid']})
            return read(path)
        with patch.object(Path, 'read_text', metadata):
            self.assertEqual(0, curl.main(['abcdef123456', 'https://example-shop.invalid/path']))
            self.assertEqual(1, len(self.headers))
            for url in ['https://example-shop.invalid.evil.test/', 'http://example-shop.invalid', 'https://example-shop.invalid:8443']:
                with self.assertRaises(ValueError):
                    curl.main(['abcdef123456', url])
            self.assertEqual(1, len(self.headers))


if __name__ == '__main__':
    unittest.main()
