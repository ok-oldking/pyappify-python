import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import pyappify
from pyappify import standalone, standalone_updater
from pyappify.versions import is_release_version, sort_version_tags

try:
    import dulwich  # noqa: F401
    HAVE_DULWICH = True
except ImportError:
    HAVE_DULWICH = False

HAVE_GIT = shutil.which("git") is not None

PYAPPIFY_YML = """\
name: "demo"
profiles:
  - name: "China"
    git_url: "https://example.invalid/demo.git"
    requirements: "requirements.txt" # pinned
    pip_args: "--no-deps"
  - name: "Global"
    main_script: "main.py"
"""


def git(cwd, *args):
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
            "-c", "core.autocrlf=false", *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def read(path):
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


class TestVersionTags(unittest.TestCase):
    def test_sorts_like_the_launcher(self):
        tags = ["v1.0.0", "notes", "v1.1.0-beta", "v1.1.0", "1.1.0-rc.2", "v1.1.0.rc", "v1.1.0-alpha.1"]
        self.assertEqual(
            ["v1.1.0", "1.1.0-rc.2", "v1.1.0.rc", "v1.1.0-beta", "v1.1.0-alpha.1", "v1.0.0"],
            sort_version_tags(tags),
        )

    def test_release_versions(self):
        self.assertTrue(is_release_version("1.5.0"))
        self.assertTrue(is_release_version("v1.5.0"))
        self.assertFalse(is_release_version("1.5.0-beta"))
        self.assertFalse(is_release_version("v1.5.0.beta"))
        self.assertFalse(is_release_version("latest"))


class TestProfileParsing(unittest.TestCase):
    def test_parses_profiles_without_yaml(self):
        config = standalone._parse_app_config_without_yaml(PYAPPIFY_YML)
        profiles = config["profiles"]

        self.assertEqual("demo", config["name"])
        self.assertEqual("China", profiles[0]["name"])
        self.assertEqual("requirements.txt", profiles[0]["requirements"])
        self.assertEqual("--no-deps", profiles[0]["pip_args"])
        self.assertEqual({"name": "Global", "main_script": "main.py"}, profiles[1])


@unittest.skipUnless(HAVE_DULWICH and HAVE_GIT, "requires dulwich and git")
class StandaloneRepoTestCase(unittest.TestCase):
    """A remote with version tags and a clone of it checked out at v1.0.0."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.remote = os.path.join(self.root, "remote")
        self.clone = os.path.join(self.root, "clone")
        os.makedirs(self.remote)
        git(self.remote, "init", "-q", "-b", "main")

        write(os.path.join(self.remote, "pyappify.yml"), PYAPPIFY_YML)
        write(os.path.join(self.remote, "requirements.txt"), "six==1.16.0\n")
        write(os.path.join(self.remote, "app.txt"), "1.0.0\n")
        self.commit("Initial release")
        git(self.remote, "tag", "v1.0.0")

        self.commit_file("app.txt", "1.1.0-beta\n", "Beta feature\n\nMore detail")
        git(self.remote, "tag", "-a", "v1.1.0-beta", "-m", "beta")

        git(self.remote, "checkout", "-q", "-b", "side")
        self.commit_file("side.txt", "side\n", "Side change")
        git(self.remote, "checkout", "-q", "main")
        git(self.remote, "merge", "-q", "--no-ff", "side", "-m", "Merge side")
        self.commit_file("app.txt", "1.1.0\n", "Release 1.1.0")
        git(self.remote, "tag", "v1.1.0")

        git(self.remote, "clone", "-q", self.remote, self.clone)
        git(self.clone, "checkout", "-q", "v1.0.0")

        self.saved = (pyappify.pyappify_version, pyappify.pyappify_executable, pyappify.app_version)
        pyappify.pyappify_version = None
        pyappify.pyappify_executable = None
        pyappify.app_version = None
        env = mock.patch.dict(os.environ, {"PYAPPIFY_REPO_PATH": self.clone})
        env.start()
        os.environ.pop("PYAPPIFY_PYTHON_TEST", None)
        self.addCleanup(env.stop)

    def tearDown(self):
        pyappify.pyappify_version, pyappify.pyappify_executable, pyappify.app_version = self.saved
        shutil.rmtree(self.root, ignore_errors=True)

    def commit(self, message):
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-q", "-m", message)

    def commit_file(self, name, text, message):
        write(os.path.join(self.remote, name), text)
        self.commit(message)

    def head(self):
        with standalone.open_repo(self.clone) as repo:
            return repo.head()

    def tag_commit(self, tag):
        with standalone.open_repo(self.clone) as repo:
            return standalone.tag_commit(repo, tag)

    def add_remote_release(self, tag, requirements=None, orphan=False):
        """Tag a new release on the remote, optionally on a commit off every branch."""
        if orphan:
            git(self.remote, "checkout", "-q", "--detach")
        if requirements is not None:
            write(os.path.join(self.remote, "requirements.txt"), requirements)
        self.commit_file("app.txt", tag + "\n", "Release " + tag)
        git(self.remote, "tag", tag)
        if orphan:
            git(self.remote, "checkout", "-q", "main")


class TestStandaloneVersionList(StandaloneRepoTestCase):
    def test_uses_standalone_backend_without_launcher(self):
        self.assertFalse(pyappify.updates.uses_launcher())
        with mock.patch.object(pyappify.launcher, "_run_launcher_api") as run:
            versions = pyappify.get_version_list(release_only=False)

        run.assert_not_called()
        self.assertEqual(["v1.1.0", "v1.1.0-beta", "v1.0.0"], [v["version"] for v in versions])
        self.assertEqual("v1.1.0-beta", versions[0]["previous_version"])
        self.assertIsNone(versions[2]["previous_version"])

    def test_notes_skip_merges_and_split_message_lines(self):
        versions = pyappify.get_version_list(release_only=False)

        self.assertCountEqual(["Release 1.1.0", "Side change"], versions[0]["update_note"])
        self.assertEqual(["Beta feature", "More detail"], versions[1]["update_note"])
        self.assertEqual(["Initial release"], versions[2]["update_note"])

    def test_release_only_compares_against_previous_release(self):
        versions = pyappify.get_version_list(release_only=True)

        self.assertEqual(["v1.1.0", "v1.0.0"], [v["version"] for v in versions])
        self.assertEqual("v1.0.0", versions[0]["previous_version"])
        self.assertIn("Beta feature", versions[0]["update_note"])

    def test_fetches_new_tags_including_ones_off_branches_and_prunes_deleted(self):
        self.add_remote_release("v1.2.0", orphan=True)
        git(self.remote, "tag", "-d", "v1.1.0-beta")

        versions = pyappify.get_version_list(number_versions=2, release_only=False)

        self.assertEqual(["v1.2.0", "v1.1.0"], [v["version"] for v in versions])
        self.assertEqual("v1.0.0", versions[1]["previous_version"])

    def test_detects_version_tag_at_head(self):
        self.assertEqual("v1.0.0", standalone.detect_app_version())


class TestStandaloneUpdateRequest(StandaloneRepoTestCase):
    def test_writes_job_and_starts_updater(self):
        with mock.patch.object(standalone, "_start_updater") as start:
            result = pyappify.update_to_version("v1.1.0")

        job_path = start.call_args.args[0]
        job = json.loads(read(job_path))
        # Test commits share timestamps, so only membership is stable.
        self.assertCountEqual(
            ["Release 1.1.0", "Side change", "Beta feature", "More detail"],
            result.pop("update_note"),
        )
        self.assertEqual(
            {"updated": True, "version": "v1.1.0", "restart_required": True}, result
        )
        self.assertEqual(os.path.join(self.clone, ".git", "pyappify", "update-job.json"), job_path)
        self.assertEqual("v1.0.0", job["previous_version"])
        self.assertEqual(self.head().decode(), job["previous_commit"])
        self.assertEqual(os.getpid(), job["pid"])
        self.assertEqual(sys.executable, job["relaunch"]["argv"][0])
        self.assertTrue(job["refresh_shortcuts"])
        self.assertEqual(self.tag_commit("v1.0.0"), self.head(), "the app's files must not change yet")

    def test_rejects_unknown_version(self):
        with mock.patch.object(standalone, "_start_updater") as start:
            with self.assertRaisesRegex(standalone.StandaloneUpdateError, "v9.9.9"):
                pyappify.update_to_version("v9.9.9")
        start.assert_not_called()

    def test_rejects_local_changes(self):
        write(os.path.join(self.clone, "app.txt"), "edited\n")
        with mock.patch.object(standalone, "_start_updater") as start:
            with self.assertRaisesRegex(standalone.StandaloneUpdateError, "local changes"):
                pyappify.update_to_version("v1.1.0")
        start.assert_not_called()

    def test_untracked_files_do_not_block_update(self):
        write(os.path.join(self.clone, "notes.log"), "untracked\n")
        with mock.patch.object(standalone, "_start_updater") as start:
            pyappify.update_to_version("v1.1.0")
        start.assert_called_once()


class TestStandaloneUpdater(StandaloneRepoTestCase):
    def job(self, version, **overrides):
        job = {
            "repo_path": self.clone,
            "version": version,
            "previous_version": "v1.0.0",
            "previous_commit": self.head().decode(),
            "update_note": ["Release " + version],
            "profile": "Global",
            "pid": None,
            "relaunch": {"argv": None, "cwd": self.clone, "pythonpath": None},
        }
        job.update(overrides)
        return job

    def run_job(self, job, pip_error=None):
        log = mock.Mock()
        with mock.patch.object(
            standalone_updater, "pip_install", side_effect=pip_error
        ) as pip, mock.patch.object(standalone_updater, "relaunch") as relaunch:
            result = standalone_updater.run_job(job, log)
        return result, pip, relaunch

    def test_checks_out_tag_and_skips_pip_when_requirements_unchanged(self):
        succeeded, pip, relaunch = self.run_job(self.job("v1.1.0"))

        self.assertTrue(succeeded)
        self.assertEqual(self.tag_commit("v1.1.0"), self.head())
        self.assertEqual("1.1.0\n", read(os.path.join(self.clone, "app.txt")))
        pip.assert_not_called()
        relaunch.assert_called_once()
        self.assertEqual(
            {
                "PYAPPIFY_APP_VERSION": "v1.1.0",
                "PYAPPIFY_APP_STARTING_VERSION": "v1.0.0",
                "PYAPPIFY_UPDATE_NOTE": '["Release v1.1.0"]',
            },
            relaunch.call_args.args[1],
        )

    def test_syncs_requirements_with_inherited_profile_settings(self):
        self.add_remote_release("v1.2.0", requirements="six==1.17.0\n")
        standalone.fetch_tags(self.clone)

        succeeded, pip, _relaunch = self.run_job(self.job("v1.2.0"))

        self.assertTrue(succeeded)
        pip.assert_called_once_with(self.clone, "requirements.txt", "--no-deps", mock.ANY)

    def test_rolls_back_when_pip_fails(self):
        self.add_remote_release("v1.2.0", requirements="six==1.17.0\n")
        standalone.fetch_tags(self.clone)
        before = self.head()

        succeeded, _pip, relaunch = self.run_job(
            self.job("v1.2.0"), pip_error=standalone.StandaloneUpdateError("pip failed")
        )

        self.assertFalse(succeeded)
        self.assertEqual(before, self.head())
        self.assertEqual("1.0.0\n", read(os.path.join(self.clone, "app.txt")))
        relaunch.assert_called_once_with(mock.ANY, {}, mock.ANY)

    def test_relaunch_restores_environment_and_sets_update_variables(self):
        job = self.job("v1.1.0", relaunch={
            "argv": [sys.executable, "-c", "pass"], "cwd": self.clone, "pythonpath": None,
        })
        with mock.patch.dict(os.environ, {"PYTHONPATH": "added-for-updater"}), \
                mock.patch.object(standalone_updater.subprocess, "Popen") as popen:
            standalone_updater.relaunch(job, {"PYAPPIFY_APP_VERSION": "v1.1.0"}, mock.Mock())

        env = popen.call_args.kwargs["env"]
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual("v1.1.0", env["PYAPPIFY_APP_VERSION"])
        self.assertEqual(self.clone, popen.call_args.kwargs["cwd"])

    def test_command_line_updater_waits_for_app_then_restarts_it(self):
        """Runs ``python -m pyappify.standalone_updater`` as a real process."""
        marker = os.path.join(self.root, "relaunched.json")
        app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        self.addCleanup(app.kill)
        relaunch_python = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(relaunch_python):
            relaunch_python = sys.executable
        job = self.job("v1.1.0", pid=app.pid, relaunch={
            "argv": [
                relaunch_python, "-c",
                "import json, os, sys; json.dump({k: v for k, v in os.environ.items() "
                "if k.startswith('PYAPPIFY_APP_') or k == 'PYAPPIFY_UPDATE_NOTE'}, "
                "open(sys.argv[1], 'w'))",
                marker,
            ],
            "cwd": self.root,
            "pythonpath": None,
        })
        job_dir = os.path.join(self.clone, ".git", "pyappify")
        os.makedirs(job_dir)
        job_path = os.path.join(job_dir, "update-job.json")
        write(job_path, json.dumps(job))
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(pyappify.__file__)))
        env.pop("PYAPPIFY_REPO_PATH")

        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-m", "pyappify.standalone_updater", job_path],
            cwd=self.clone, env=env, capture_output=True, text=True, timeout=60,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIsNotNone(app.poll(), "updater returned before the app exited")
        self.assertGreater(time.monotonic() - started, 1.0)
        self.assertIn("Updated to v1.1.0.", completed.stdout)
        self.assertEqual(self.tag_commit("v1.1.0"), self.head())
        self.assertFalse(os.path.exists(job_path))
        self.assertIn("Updated to v1.1.0.", read(os.path.join(job_dir, "update.log")))
        deadline = time.monotonic() + 10
        while not os.path.exists(marker) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(
            {
                "PYAPPIFY_APP_VERSION": "v1.1.0",
                "PYAPPIFY_APP_STARTING_VERSION": "v1.0.0",
                "PYAPPIFY_UPDATE_NOTE": '["Release v1.1.0"]',
            },
            json.loads(read(marker)),
        )


if __name__ == "__main__":
    unittest.main()
