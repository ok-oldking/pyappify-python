import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pyappify


EXECUTABLE_SHA256 = "50ef2557c193ca1f97c1e699bab3dad35b3966c9e8e8b860fb609bd5fb3861d1"


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_upgrade_kills_running_executable_and_replaces_it(
    test_case, zip_url, timeout_seconds
):
    source_executable = Path(os.environ["WINDIR"]) / "System32" / "PING.EXE"
    test_case.assertTrue(source_executable.exists(), "Windows PING.EXE is required")

    old_cwd = os.getcwd()
    old_upgradeable = pyappify.pyappify_upgradeable
    old_version = pyappify.pyappify_version
    old_executable = pyappify.pyappify_executable
    old_pid = pyappify.pid
    old_logger = pyappify.logger

    with tempfile.TemporaryDirectory() as temporary_directory:
        executable = Path(temporary_directory) / "ok-ww.exe"
        upgrade_temporary_directory = Path(temporary_directory) / "pyappify_tmp"
        shutil.copy2(source_executable, executable)
        test_case.assertNotEqual(_sha256(executable), EXECUTABLE_SHA256)

        process = subprocess.Popen(
            [str(executable), "-t", "127.0.0.1"],
            cwd=temporary_directory,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(1)
            test_case.assertIsNone(
                process.poll(), "the current executable exited before upgrade began"
            )

            pyappify.pyappify_upgradeable = True
            pyappify.pyappify_version = "v3.3.45"
            pyappify.pyappify_executable = str(executable)
            pyappify.pid = process.pid
            pyappify.logger = logging.getLogger(__name__)
            os.chdir(temporary_directory)

            pyappify.upgrade("v3.3.46", EXECUTABLE_SHA256, [zip_url])

            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                process_stopped = process.poll() is not None
                upgraded = (
                    executable.exists()
                    and _sha256(executable) == EXECUTABLE_SHA256
                )
                update_finished = not upgrade_temporary_directory.exists()
                if process_stopped and upgraded and update_finished:
                    break
                time.sleep(0.1)

            test_case.assertIsNotNone(
                process.poll(), "upgrade did not stop the old process"
            )
            test_case.assertEqual(EXECUTABLE_SHA256, _sha256(executable))
        finally:
            os.chdir(old_cwd)
            pyappify.pyappify_upgradeable = old_upgradeable
            pyappify.pyappify_version = old_version
            pyappify.pyappify_executable = old_executable
            pyappify.pid = old_pid
            pyappify.logger = old_logger
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


class TestUpgrade(unittest.TestCase):
    LOCAL_ZIP = Path(__file__).with_name("ok-ww-win32.zip")

    def test_upgrade_worker_stops_when_exit_event_is_already_set(self):
        exit_event = threading.Event()
        exit_event.set()

        with mock.patch.object(
            pyappify, "pyappify_upgradeable", True
        ), mock.patch.object(
            pyappify, "pyappify_version", "v1.0.0"
        ), mock.patch.object(pyappify.urllib.request, "urlopen") as urlopen:
            thread = pyappify.upgrade(
                "v1.0.1",
                "",
                ["https://example.invalid/pyappify.zip"],
                exit_event=exit_event,
            )
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        urlopen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "executable replacement test is Windows-only")
    def test_upgrade_from_local_zip_kills_running_executable_and_replaces_it(self):
        self.assertTrue(self.LOCAL_ZIP.exists(), "tests/ok-ww-win32.zip is required")
        assert_upgrade_kills_running_executable_and_replaces_it(
            self,
            self.LOCAL_ZIP.as_uri(), timeout_seconds=30
        )


if __name__ == "__main__":
    unittest.main()
