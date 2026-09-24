import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest


BUILD = Path(__file__).resolve().parents[1] / 'docker-build.sh'


class HarnessInstallTest(unittest.TestCase):
    def test_antigravity_installer_handles_plain_and_compressed_responses(self):
        source = BUILD.read_text()
        download = next(line for line in source.splitlines() if 'https://antigravity.google/cli/install.sh' in line)
        install = source[source.index(download):].splitlines()[:2]
        script = b'#!/bin/bash\nprintf "installer executed\\n"\n'

        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                body = gzip.compress(script) if compressed else script

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/x-sh')
                        if compressed:
                            self.send_header('Content-Encoding', 'gzip')
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                    def log_message(self, format, *args):
                        pass

                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    command = '\n'.join(install).replace(
                        'https://antigravity.google/cli/install.sh',
                        f'http://127.0.0.1:{server.server_port}/install.sh',
                    )
                    with tempfile.TemporaryDirectory() as directory:
                        result = subprocess.run(['bash', '-eu', '-c', command], cwd=directory,
                                                capture_output=True, text=True, timeout=10)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual('installer executed\n', result.stdout)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


if __name__ == '__main__':
    unittest.main()
