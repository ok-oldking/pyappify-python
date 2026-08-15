import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pyappify


class TestLauncherApi(unittest.TestCase):
    def setUp(self):
        self.old_executable = pyappify.pyappify_executable

    def tearDown(self):
        pyappify.pyappify_executable = self.old_executable

    def test_finds_configured_executable_before_ancestor_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            configured = root / "configured.exe"
            configured.touch()
            fallback = root / "pyappify.exe"
            fallback.touch()

            found = pyappify.find_pyappify_executable(
                start_dir=root / "nested" / "working",
                environ={"PYAPPIFY_EXECUTABLE": str(configured)},
            )

        self.assertEqual(str(configured.resolve()), found)

    def test_finds_executable_by_walking_parent_directories(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            executable = root / "pyappify.exe"
            executable.touch()
            working = root / "data" / "apps" / "example" / "working"
            working.mkdir(parents=True)

            found = pyappify.find_pyappify_executable(
                start_dir=working,
                environ={},
            )

        self.assertEqual(str(executable.resolve()), found)

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

    def test_update_to_version_returns_launcher_result(self):
        pyappify.pyappify_executable = os.path.abspath("pyappify.exe")
        with mock.patch.object(pyappify.os.path, "isfile", return_value=True), mock.patch.object(
            pyappify, "_run_launcher_api", return_value={"updated": True, "version": "v2.0.0"}
        ) as run:
            result = pyappify.update_to_version("v2.0.0")

        self.assertEqual({"updated": True, "version": "v2.0.0"}, result)
        run.assert_called_once_with(["--update-to-version", "v2.0.0"], timeout=300)

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
