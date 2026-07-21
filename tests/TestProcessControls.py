import os
import signal
import unittest
from unittest import mock

import pyappify


class TestProcessControls(unittest.TestCase):
    def setUp(self):
        self.old_pid = pyappify.pid
        self.old_executable = pyappify.pyappify_executable
        self.old_logger = pyappify.logger
        pyappify.logger = mock.Mock()

    def tearDown(self):
        pyappify.pid = self.old_pid
        pyappify.pyappify_executable = self.old_executable
        pyappify.logger = self.old_logger

    def test_kill_pyappify_exe_terminates_configured_pid(self):
        pyappify.pid = 1234

        with mock.patch.object(pyappify.os, "kill") as kill, mock.patch.object(
            pyappify, "_wait_for_process_exit", return_value=True
        ) as wait:
            self.assertTrue(pyappify.kill_pyappify_exe(timeout=5))

        kill.assert_called_once_with(1234, signal.SIGTERM)
        wait.assert_called_once_with(1234, 5)

    def test_show_pyappify_brings_existing_window_to_front(self):
        pyappify.pid = 1234

        with mock.patch.object(
            pyappify, "_is_process_running", return_value=True
        ) as is_running, mock.patch.object(
            pyappify, "bring_window_to_front_by_pid", return_value=True
        ) as bring_to_front, mock.patch.object(pyappify.subprocess, "Popen") as popen:
            self.assertEqual(1234, pyappify.show_pyappify())

        is_running.assert_called_once_with(1234)
        bring_to_front.assert_called_once_with(1234)
        popen.assert_not_called()

    def test_show_pyappify_starts_executable_when_not_running(self):
        executable = os.path.abspath("pyappify.exe")
        pyappify.pid = 1234
        pyappify.pyappify_executable = executable
        process = mock.Mock()
        process.pid = 5678

        with mock.patch.object(
            pyappify, "_is_process_running", return_value=False
        ), mock.patch.object(pyappify.subprocess, "Popen", return_value=process) as popen:
            self.assertEqual(
                5678,
                pyappify.show_pyappify(args=["--profile", "dev"]),
            )

        popen.assert_called_once_with(
            [executable, "--profile", "dev"],
            cwd=os.path.dirname(executable),
            env=None,
        )
        self.assertEqual(5678, pyappify.pid)

    def test_show_pyappify_returns_none_without_executable(self):
        pyappify.pid = None
        pyappify.pyappify_executable = None

        with mock.patch.object(pyappify, "_is_process_running", return_value=False):
            self.assertIsNone(pyappify.show_pyappify())


if __name__ == "__main__":
    unittest.main()
