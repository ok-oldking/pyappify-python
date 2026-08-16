import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import pyappify


class TestLauncherApi(unittest.TestCase):
    def setUp(self):
        self.old_executable = pyappify.pyappify_executable
        self.old_pyappify_version = pyappify.pyappify_version
        pyappify.pyappify_version = "1.2.2"

    def tearDown(self):
        pyappify.pyappify_executable = self.old_executable
        pyappify.pyappify_version = self.old_pyappify_version

    def test_get_version_list_rejects_missing_pyappify_version_without_calling_launcher(self):
        pyappify.pyappify_version = None
        with mock.patch.object(pyappify, "_run_launcher_api") as run:
            with self.assertRaisesRegex(
                RuntimeError, r"does not support checking for updates.*None"
            ):
                pyappify.get_version_list()

        run.assert_not_called()

    def test_get_version_list_rejects_old_pyappify_version_without_calling_launcher(self):
        pyappify.pyappify_version = "v1.2.1"
        with mock.patch.object(pyappify, "_run_launcher_api") as run:
            with self.assertRaisesRegex(
                RuntimeError, r"does not support checking for updates.*v1\.2\.1"
            ):
                pyappify.get_version_list()

        run.assert_not_called()

    def test_finds_configured_executable_before_ancestor_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            configured = root / "configured.exe"
            configured.touch()
            fallback = root / "example.exe"
            fallback.touch()

            found = pyappify.find_pyappify_executable(
                start_dir=root / "data" / "apps" / "example" / "working",
                environ={"PYAPPIFY_EXECUTABLE": str(configured)},
            )

        self.assertEqual(str(configured.resolve()), found)

    def test_finds_executable_by_walking_parent_directories(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            executable = root / "example.exe"
            executable.touch()
            working = root / "data" / "apps" / "example" / "working"
            working.mkdir(parents=True)

            found = pyappify.find_pyappify_executable(
                start_dir=working,
                environ={},
            )

        self.assertEqual(str(executable.resolve()), found)

    def test_finds_app_launcher_from_a_directory_below_working(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            executable = root / "Example App.exe"
            executable.touch()
            nested = root / "data" / "apps" / "Example App" / "working" / "src"
            nested.mkdir(parents=True)

            found = pyappify.find_pyappify_executable(start_dir=nested, environ={})

        self.assertEqual(str(executable.resolve()), found)

    def test_does_not_use_prefixed_app_executable_name(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "example Launcher.exe").touch()
            working = root / "data" / "apps" / "example" / "working"
            working.mkdir(parents=True)

            found = pyappify.find_pyappify_executable(start_dir=working, environ={})

        self.assertIsNone(found)

    def test_does_not_fall_back_to_pyappify_executable(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "pyappify.exe").touch()
            working = root / "data" / "apps" / "example" / "working"
            working.mkdir(parents=True)

            found = pyappify.find_pyappify_executable(start_dir=working, environ={})

        self.assertIsNone(found)

    def test_does_not_search_above_app_root(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            executable = root / "example.exe"
            executable.touch()
            app_root = root / "installed-app"
            working = app_root / "data" / "apps" / "example" / "working"
            working.mkdir(parents=True)

            found = pyappify.find_pyappify_executable(start_dir=working, environ={})

        self.assertIsNone(found)

    def test_invalid_configured_executable_does_not_use_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            launcher = root / "example.exe"
            launcher.touch()
            working = root / "data" / "apps" / "example" / "working"
            working.mkdir(parents=True)

            found = pyappify.find_pyappify_executable(
                start_dir=working,
                environ={"PYAPPIFY_EXECUTABLE": str(root / "missing.exe")},
            )

        self.assertIsNone(found)

    def test_missing_executable_raises_a_clear_error(self):
        pyappify.pyappify_executable = None
        with mock.patch.object(pyappify, "find_pyappify_executable", return_value=None):
            with self.assertRaises(FileNotFoundError):
                pyappify.get_version_list()

    def test_get_version_list_uses_json_response_and_executable_directory(self):
        with tempfile.TemporaryDirectory() as root:
            executable = os.path.join(root, "pyappify.exe")
            Path(executable).touch()
            pyappify.pyappify_executable = executable
            expected = [
                {
                    "version": "v1.2.0",
                    "previous_version": "v1.1.0",
                    "update_note": ["new feature"],
                }
            ]

            def write_response(command, **kwargs):
                response_path = command[command.index("--response-file") + 1]
                with open(response_path, "w", encoding="utf-8") as response_file:
                    json.dump(expected, response_file)
                return mock.Mock(pid=1234)

            with mock.patch.object(
                pyappify.subprocess, "Popen", side_effect=write_response
            ) as popen:
                actual = pyappify.get_version_list(4, release_only=False, timeout=1)

        self.assertEqual(expected, actual)
        command = popen.call_args.args[0]
        self.assertEqual(executable, command[0])
        self.assertIn("--number-versions", command)
        self.assertIn("4", command)
        self.assertIn("false", command)
        self.assertEqual(root, popen.call_args.kwargs["cwd"])

    def test_get_version_list_has_a_two_minute_default_timeout(self):
        with mock.patch.object(
            pyappify, "_run_launcher_api", return_value=[]
        ) as run:
            result = pyappify.get_version_list()

        self.assertEqual([], result)
        run.assert_called_once_with(
            [
                "--get-version-list",
                "--number-versions",
                "10",
                "--release-only",
                "true",
            ],
            timeout=120,
            exit_event=None,
        )

    def test_get_version_list_returns_timeout_as_an_error(self):
        with mock.patch.object(
            pyappify,
            "_run_launcher_api",
            side_effect=TimeoutError("Timed out waiting for a response from PyAppify"),
        ):
            with self.assertRaisesRegex(TimeoutError, "Timed out waiting"):
                pyappify.get_version_list()

    def test_launcher_request_terminates_when_exit_event_is_set(self):
        exit_event = threading.Event()
        process = mock.Mock()
        process.poll.return_value = None
        pyappify.pyappify_executable = os.path.abspath("pyappify.exe")

        def request_exit(_event, _timeout):
            exit_event.set()
            return True

        with mock.patch.object(
            pyappify.os.path, "isfile", return_value=True
        ), mock.patch.object(
            pyappify.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            pyappify, "_wait_for_exit", side_effect=request_exit
        ):
            with self.assertRaisesRegex(InterruptedError, "exit_event"):
                pyappify.get_version_list(exit_event=exit_event)

        process.terminate.assert_called_once_with()

    def test_update_to_version_returns_launcher_result(self):
        pyappify.pyappify_executable = os.path.abspath("pyappify.exe")
        with mock.patch.object(pyappify.os.path, "isfile", return_value=True), mock.patch.object(
            pyappify, "_run_launcher_api", return_value={"updated": True, "version": "v2.0.0"}
        ) as run:
            result = pyappify.update_to_version("v2.0.0")

        self.assertEqual({"updated": True, "version": "v2.0.0"}, result)
        run.assert_called_once_with(
            ["--update-to-version", "v2.0.0"],
            timeout=300,
            exit_event=None,
        )

    def test_calculate_update_notes_returns_inclusive_descending_range(self):
        versions = [
            {"version": "v0.9.9", "update_note": ["note 0.9.9"]},
            {"version": "v0.9.8", "update_note": ["note 0.9.8"]},
            {"version": "v0.9.7", "update_note": ["note 0.9.7"]},
            {"version": "v0.9.6", "update_note": ["note 0.9.6"]},
        ]

        notes = pyappify.calculate_update_notes(versions, "v0.9.7", "v0.9.9")

        self.assertEqual(["note 0.9.9", "note 0.9.8", "note 0.9.7"], notes)

    def test_calculate_update_notes_excludes_versions_above_target_when_current_is_missing(self):
        versions = [
            {"version": "v3.0.0", "update_note": ["three"]},
            {"version": "v2.0.0", "update_note": ["two"]},
            {"version": "v1.0.0", "update_note": "one"},
        ]

        notes = pyappify.calculate_update_notes(versions, "dev", "v2.0.0")

        self.assertEqual(["two", "one"], notes)

    def test_test_environment_returns_mocked_data_without_launcher(self):
        with mock.patch.dict(
            os.environ,
            {"PYAPPIFY_PYTHON_TEST": "1"},
        ), mock.patch.object(pyappify, "app_version", "v1.2.3"), mock.patch.object(
            pyappify, "_run_launcher_api"
        ) as run, mock.patch.object(pyappify.time, "sleep") as sleep:
            versions = pyappify.get_version_list(3)
            result = pyappify.update_to_version("v1.2.4")

        self.assertEqual(["v100.1.1", "v1.2.5", "v1.2.4"], [v["version"] for v in versions])
        self.assertEqual("v100.1.0", versions[0]["previous_version"])
        self.assertEqual(
            {"updated": True, "version": "v1.2.4", "mocked": True},
            result,
        )
        run.assert_not_called()
        sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
