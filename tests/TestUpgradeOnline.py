import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

from tests.TestUpgrade import assert_upgrade_kills_running_executable_and_replaces_it


class TestUpgradeOnline(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "executable replacement test is Windows-only")
    def test_upgrade_from_http_zip_kills_running_executable_and_replaces_it(self):
        # Exercise a real HTTP download without depending on an old GitHub
        # release surviving the application's release-retention policy.
        fixture_directory = Path(__file__).resolve().parent
        self.assertTrue((fixture_directory / "ok-ww-win32.zip").exists())
        handler = partial(SimpleHTTPRequestHandler, directory=str(fixture_directory))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert_upgrade_kills_running_executable_and_replaces_it(
                self,
                f"http://127.0.0.1:{server.server_port}/ok-ww-win32.zip",
                timeout_seconds=30,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
