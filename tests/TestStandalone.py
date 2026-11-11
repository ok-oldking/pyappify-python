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
from pyappify.versions import is_release_version, select_kept_tags, sort_version_tags

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


def tag_versions(versions):
    return [version for version in versions if version["kind"] == "tag"]


class TestVersionTags(unittest.TestCase):
    def test_sorts_like_the_launcher(self):
        tags = ["v1.0.0", "notes", "v1.1.0-beta", "v1.1.0", "1.1.0-rc.2", "v1.1.0.rc", "v1.1.0-alpha.1"]
        self.assertEqual(
            ["v1.1.0", "1.1.0-rc.2", "v1.1.0.rc", "v1.1.0-beta", "v1.1.0-alpha.1", "v1.0.0"],
            sort_version_tags(tags),
        )

    def test_keeps_newest_releases_and_newer_prereleases(self):
        tags = ["v1.0.0", "v1.1.0-beta.1", "v1.1.0", "v1.2.0", "v1.3.0-beta.2", "latest"]
        self.assertEqual(["v1.3.0-beta.2", "v1.2.0", "v1.1.0"], select_kept_tags(tags, 2))
        self.assertEqual(["v1.3.0-beta.2", "v1.2.0"], select_kept_tags(tags, 1))
        self.assertEqual(5, len(select_kept_tags(tags, 10)))

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
        versions = tag_versions(versions)
        self.assertEqual(["v1.1.0", "v1.1.0-beta", "v1.0.0"], [v["version"] for v in versions])
        self.assertEqual("v1.1.0-beta", versions[0]["previous_version"])
        self.assertIsNone(versions[2]["previous_version"])

    def test_notes_skip_merges_and_split_message_lines(self):
        versions = tag_versions(pyappify.get_version_list(release_only=False))

        self.assertCountEqual(["Release 1.1.0", "Side change"], versions[0]["update_note"])
        self.assertEqual(["Beta feature", "More detail"], versions[1]["update_note"])
        self.assertEqual(["Initial release"], versions[2]["update_note"])

    def test_release_only_compares_against_previous_release(self):
        versions = pyappify.get_version_list(release_only=True)

        self.assertEqual(["v1.1.0", "v1.0.0"], [v["version"] for v in versions])
        self.assertEqual("v1.0.0", versions[0]["previous_version"])
        self.assertIn("Beta feature", versions[0]["update_note"])

    def test_fetches_new_tags_including_ones_off_branches(self):
        self.add_remote_release("v1.2.0", orphan=True)

        versions = tag_versions(pyappify.get_version_list(number_versions=2, release_only=False))

        self.assertEqual(["v1.2.0", "v1.1.0"], [v["version"] for v in versions])
        self.assertEqual("v1.1.0-beta", versions[1]["previous_version"])

    def local_tags(self):
        with standalone.open_repo(self.clone) as repo:
            return sorted(name.decode() for name in repo.refs.as_dict(standalone.TAG_PREFIX))

    def test_plain_clone_keeps_every_tag(self):
        """A developer's clone ignores keep_releases and loses no tag."""
        git(self.clone, "config", "pyappify.keepreleases", "1")
        write(
            os.path.join(self.remote, "pyappify.yml"),
            "keep_releases: 1\n" + PYAPPIFY_YML,
        )
        self.add_remote_release("v1.2.0")
        self.add_remote_release("v1.3.0-beta")
        git(self.clone, "fetch", "-q", "--tags")
        git(self.clone, "checkout", "-q", "v1.2.0")
        git(self.clone, "tag", "v9.9.9-local")
        git(self.remote, "tag", "-d", "v1.1.0-beta")
        self.add_remote_release("v1.3.0")

        updated, pruned = standalone.fetch_tags(self.clone)

        self.assertEqual((["v1.3.0"], []), (updated, pruned))
        self.assertEqual(
            ["v1.0.0", "v1.1.0", "v1.1.0-beta", "v1.2.0", "v1.3.0", "v1.3.0-beta", "v9.9.9-local"],
            self.local_tags(),
        )

    def test_detects_version_tag_at_head(self):
        self.assertEqual("v1.0.0", standalone.detect_app_version())

    def lock_new_tag(self, age_seconds):
        """A lock on a tag the next fetch writes, as an app killed mid-fetch leaves."""
        self.add_remote_release("v1.2.0")
        lock = os.path.join(self.clone, ".git", "refs", "tags", "v1.2.0.lock")
        write(lock, "")
        modified = time.time() - age_seconds
        os.utime(lock, (modified, modified))
        return lock

    def test_stale_ref_lock_is_removed(self):
        lock = self.lock_new_tag(age_seconds=3600)

        updated, _pruned = standalone.fetch_tags(self.clone)

        self.assertEqual(["v1.2.0"], updated)
        self.assertEqual(["v1.0.0", "v1.1.0", "v1.1.0-beta", "v1.2.0"], self.local_tags())
        self.assertFalse(os.path.exists(lock))

    def test_fresh_ref_lock_is_reported(self):
        lock = self.lock_new_tag(age_seconds=0)

        with self.assertRaisesRegex(standalone.StandaloneUpdateError, "another process"):
            standalone.fetch_tags(self.clone)

        self.assertTrue(os.path.exists(lock))


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


class TestBranchVersions(StandaloneRepoTestCase):
    """The default branch, local branches and pull requests of a plain clone."""

    def setUp(self):
        super().setUp()
        # The clone is at v1.0.0; the remote gets a commit and a pull request.
        git(self.clone, "branch", "feature", "v1.1.0-beta")
        self.commit_file("app.txt", "main\n", "Unreleased work")
        git(self.remote, "checkout", "-q", "-b", "pull", "v1.1.0")
        self.commit_file("pull.txt", "pull\n", "Proposed change")
        git(self.remote, "update-ref", "refs/pull/7/head", "HEAD")
        git(self.remote, "checkout", "-q", "main")
        git(self.remote, "branch", "-q", "-D", "pull")
        pulls = mock.patch.object(
            standalone, "list_pull_requests", return_value=[(7, "Add a thing")]
        )
        pulls.start()
        self.addCleanup(pulls.stop)

    def ref(self, name):
        with standalone.open_repo(self.clone) as repo:
            return repo.refs[name]

    def head_branch(self):
        with standalone.open_repo(self.clone) as repo:
            return standalone.head_branch(repo)

    def job(self, version, **overrides):
        job = {
            "repo_path": self.clone,
            "version": version,
            "previous_version": "v1.0.0",
            "previous_commit": self.head().decode(),
            "previous_ref": None,
            "update_note": ["note"],
            "profile": "Global",
            "pid": None,
            "relaunch": {"argv": None, "cwd": self.clone, "pythonpath": None},
        }
        job.update(overrides)
        return job

    def written_job(self, start):
        """The job passed to the updater, without this process as the app to stop."""
        job = json.loads(read(start.call_args.args[0]))
        self.assertEqual(os.getpid(), job["pid"])
        job["pid"] = None
        return job

    def run_job(self, job, pip_error=None):
        log = mock.Mock()
        with mock.patch.object(
            standalone_updater, "pip_install", side_effect=pip_error
        ), mock.patch.object(standalone_updater, "relaunch") as relaunch:
            succeeded = standalone_updater.run_job(job, log)
        return succeeded, relaunch, log

    def test_detect_checkout_ref_branch_and_tag(self):
        self.assertIsNone(standalone.detect_app_ref(self.clone))
        git(self.clone, "checkout", "-q", "feature")
        for packed in (False, True):
            if packed:
                git(self.clone, "pack-refs", "--all")
            with standalone.open_repo(self.clone) as repo:
                with mock.patch.object(repo.refs, "follow", side_effect=AssertionError("resolved HEAD")):
                    self.assertEqual("feature", standalone.head_ref(repo))
            self.assertEqual("feature", standalone.detect_app_ref(self.clone))
        git(self.clone, "checkout", "-q", "v1.0.0")
        self.assertIsNone(standalone.detect_app_ref(self.clone))

    def test_detect_checkout_ref_pull_loose_and_packed(self):
        standalone.fetch_branches(self.clone, [7])
        git(self.clone, "checkout", "-q", "--detach", "refs/pyappify/pull/7")
        self.assertEqual("pr#7", standalone.detect_app_ref(self.clone))
        git(self.clone, "pack-refs", "--all")
        self.assertEqual("pr#7", standalone.detect_app_ref(self.clone))
        git(self.clone, "update-ref", "refs/pyappify/pull/7", "v1.0.0")
        self.assertIsNone(standalone.detect_app_ref(self.clone))

    def test_detect_checkout_ref_launcher_and_errors(self):
        with mock.patch.object(standalone, "is_launcher_layout", return_value=True):
            self.assertIsNone(standalone.detect_app_ref(self.clone))
        with mock.patch.object(standalone, "_dulwich", side_effect=ImportError("no dulwich")):
            self.assertIsNone(standalone.detect_app_ref(self.clone))
            self.assertEqual((None, None), standalone.detect_app_checkout(self.clone))

    def test_lists_default_branch_local_branches_pull_requests_then_tags(self):
        versions = pyappify.get_version_list(release_only=False)

        self.assertEqual(
            ["main", "local/feature", "pr/7", "v1.1.0", "v1.1.0-beta", "v1.0.0"],
            [v["version"] for v in versions],
        )
        self.assertEqual(
            ["main", "feature (local)", "#7 Add a thing", "v1.1.0", "v1.1.0-beta", "v1.0.0"],
            [v["title"] for v in versions],
        )
        self.assertEqual(
            ["branch", "local", "pr", "tag", "tag", "tag"], [v["kind"] for v in versions]
        )
        self.assertEqual(
            ["newer", "newer", "newer", "newer", "newer", "current"],
            [v["relation"] for v in versions],
        )
        self.assertEqual(self.ref(b"refs/remotes/origin/main").decode(), versions[0]["commit"])
        self.assertIn("Unreleased work", versions[0]["update_note"])
        self.assertEqual(["Beta feature", "More detail"], versions[1]["update_note"])
        self.assertIn("Proposed change", versions[2]["update_note"])

    def test_release_only_lists_tags_only(self):
        versions = pyappify.get_version_list(release_only=True)

        self.assertEqual(["v1.1.0", "v1.0.0"], [v["version"] for v in versions])

    def test_checked_out_branch_is_current_and_listed_first(self):
        git(self.clone, "branch", "other", "v1.1.0")
        git(self.clone, "checkout", "-q", "feature")

        versions = pyappify.get_version_list(release_only=False)

        self.assertEqual(["main", "local/feature", "local/other"], [v["version"] for v in versions[:3]])
        self.assertEqual("current", versions[1]["relation"])
        # v1.1.0-beta is the same commit, and a tag is not a branch to switch to.
        self.assertEqual("current", versions[-2]["relation"])
        self.assertEqual("older", versions[-1]["relation"])

    def test_closed_pull_requests_are_pruned(self):
        pyappify.get_version_list(release_only=False)
        standalone.list_pull_requests.return_value = []

        versions = pyappify.get_version_list(release_only=False)

        self.assertNotIn("pr", [v["kind"] for v in versions])
        with standalone.open_repo(self.clone) as repo:
            self.assertEqual({}, repo.refs.as_dict(standalone.LOCAL_PULL_PREFIX))

    def test_failing_pull_request_lookup_still_lists_branches(self):
        standalone.list_pull_requests.side_effect = OSError("rate limited")

        versions = pyappify.get_version_list(release_only=False)

        self.assertEqual(["main", "local/feature", "v1.1.0"], [v["version"] for v in versions[:3]])

    def test_update_to_default_branch_fast_forwards_the_local_branch(self):
        with mock.patch.object(standalone, "_start_updater") as start:
            pyappify.update_to_version("main")
        job = self.written_job(start)
        self.assertIsNone(job["previous_ref"])

        succeeded, relaunch, _log = self.run_job(job)

        self.assertTrue(succeeded)
        self.assertEqual("main", self.head_branch())
        self.assertEqual(self.ref(b"refs/remotes/origin/main"), self.head())
        self.assertEqual("main\n", read(os.path.join(self.clone, "app.txt")))
        self.assertEqual(
            {
                "PYAPPIFY_APP_VERSION": None,
                "PYAPPIFY_APP_STARTING_VERSION": None,
                "PYAPPIFY_UPDATE_NOTE": None,
            },
            relaunch.call_args.args[1],
        )

    def test_default_branch_keeps_unpushed_commits(self):
        git(self.clone, "checkout", "-q", "main")
        standalone.fetch_branches(self.clone)
        git(self.clone, "merge", "-q", "--ff-only", "origin/main")
        write(os.path.join(self.clone, "mine.txt"), "mine\n")
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "-q", "-m", "Local work")
        local = self.head()

        versions = pyappify.get_version_list(release_only=False)
        self.assertEqual(("main", "current"), (versions[0]["version"], versions[0]["relation"]))

        self.commit_file("app.txt", "main 2\n", "More remote work")
        with mock.patch.object(standalone, "_start_updater") as start:
            with self.assertRaisesRegex(standalone.StandaloneUpdateError, "diverged"):
                pyappify.update_to_version("main")
        start.assert_not_called()
        self.assertEqual(local, self.head())

    def test_update_to_local_branch_needs_no_fetch(self):
        with mock.patch.object(standalone, "fetch_tags") as fetch_tags, \
                mock.patch.object(standalone, "fetch_branches") as fetch_branches, \
                mock.patch.object(standalone, "_start_updater") as start:
            pyappify.update_to_version("local/feature")
        fetch_tags.assert_not_called()
        fetch_branches.assert_not_called()

        succeeded, _relaunch, _log = self.run_job(self.written_job(start))

        self.assertTrue(succeeded)
        self.assertEqual("feature", self.head_branch())
        self.assertEqual(self.tag_commit("v1.1.0-beta"), self.head())
        self.assertEqual("1.1.0-beta\n", read(os.path.join(self.clone, "app.txt")))

    def test_update_to_pull_request_detaches_head(self):
        git(self.clone, "checkout", "-q", "feature")
        with mock.patch.object(standalone, "_start_updater") as start:
            pyappify.update_to_version("pr/7")
        job = self.written_job(start)
        self.assertEqual("refs/heads/feature", job["previous_ref"])

        succeeded, _relaunch, _log = self.run_job(job)

        self.assertTrue(succeeded)
        self.assertIsNone(self.head_branch())
        self.assertEqual(self.ref(b"refs/pyappify/pull/7"), self.head())
        self.assertEqual("pull\n", read(os.path.join(self.clone, "pull.txt")))
        self.assertEqual(self.tag_commit("v1.1.0-beta"), self.ref(b"refs/heads/feature"))

    def test_unknown_versions_are_rejected(self):
        for version in ("local/missing", "pr/99", "no-such-branch"):
            with mock.patch.object(standalone, "_start_updater") as start:
                with self.assertRaises(standalone.StandaloneUpdateError):
                    pyappify.update_to_version(version)
            start.assert_not_called()

    def test_failed_update_returns_to_the_previous_branch(self):
        git(self.clone, "checkout", "-q", "feature")
        self.add_remote_release("v1.2.0", requirements="six==1.17.0\n")
        standalone.fetch_tags(self.clone)
        before = self.head()

        succeeded, _relaunch, _log = self.run_job(
            self.job("v1.2.0", previous_ref="refs/heads/feature"),
            pip_error=standalone.StandaloneUpdateError("pip failed"),
        )

        self.assertFalse(succeeded)
        self.assertEqual("feature", self.head_branch())
        self.assertEqual(before, self.head())
        self.assertEqual("1.1.0-beta\n", read(os.path.join(self.clone, "app.txt")))

    def test_requirements_are_synced_for_a_branch(self):
        write(os.path.join(self.remote, "requirements.txt"), "six==1.17.0\n")
        self.commit("Bump six")
        standalone.fetch_branches(self.clone)
        log = mock.Mock()

        with mock.patch.object(standalone_updater, "pip_install") as pip, \
                mock.patch.object(standalone_updater, "relaunch"):
            succeeded = standalone_updater.run_job(self.job("main"), log)

        self.assertTrue(succeeded)
        pip.assert_called_once_with(self.clone, "requirements.txt", "--no-deps", mock.ANY)

    def test_is_local_checkout(self):
        self.assertTrue(pyappify.is_local_checkout())
        pyappify.pyappify_version = "1.2.5"
        self.assertFalse(pyappify.is_local_checkout())


class TestGitHubRemote(unittest.TestCase):
    def test_recognizes_github_urls(self):
        for url in (
            "https://github.com/owner/app",
            "https://github.com/owner/app.git",
            "https://user@github.com/owner/app.git/",
            "git@github.com:owner/app.git",
            "ssh://git@github.com/owner/app",
        ):
            self.assertEqual(("owner", "app"), standalone.github_repository(url), url)
        for url in ("https://gitlab.com/owner/app.git", "https://github.com/owner", "/local/path", ""):
            self.assertIsNone(standalone.github_repository(url), url)

    def test_lists_open_pull_requests(self):
        body = json.dumps([
            {"number": 12, "title": " Fix start "},
            {"number": 9, "title": None},
        ]).encode()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = body
        with mock.patch.object(standalone.urllib.request, "urlopen", return_value=response) as urlopen:
            pulls = standalone.list_pull_requests("https://github.com/owner/app.git")
            self.assertEqual([], standalone.list_pull_requests("https://example.invalid/app.git"))

        self.assertEqual([(12, "Fix start"), (9, "")], pulls)
        urlopen.assert_called_once()
        self.assertEqual(
            "https://api.github.com/repos/owner/app/pulls?state=open&per_page=10",
            urlopen.call_args.args[0].full_url,
        )


def copy_without_git(source, destination):
    """What older launchers did: copy the clone's files into ``working``."""
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))


class TestLauncherLayout(StandaloneRepoTestCase):
    """``<app>/repo`` (git data) and ``<app>/working`` (the app's files)."""

    def setUp(self):
        super().setUp()
        self.app = os.path.join(self.root, "app")
        self.repo = os.path.join(self.app, "repo")
        self.working = os.path.join(self.app, "working")
        os.environ.pop("PYAPPIFY_REPO_PATH")

    def make_bare_layout(self):
        """The current launcher's layout, checked out at v1.0.0."""
        git(self.root, "clone", "-q", "--bare", self.remote, self.repo)
        os.makedirs(self.working)
        git(self.root, "--git-dir", self.repo, "--work-tree", self.working, "checkout", "-q", "-f", "v1.0.0")

    def make_old_layout(self):
        """An older launcher's layout: a full clone copied into ``working``."""
        git(self.root, "clone", "-q", self.remote, self.repo)
        git(self.repo, "checkout", "-q", "v1.0.0")
        copy_without_git(self.repo, self.working)

    def run_update(self, version="v1.1.0"):
        job = {
            "repo_path": self.repo,
            "version": version,
            "previous_version": "v1.0.0",
            "previous_commit": self.layout_head().decode(),
            "update_note": [],
            "profile": "Global",
            "pid": None,
            "relaunch": {"argv": None, "cwd": self.working, "pythonpath": None},
        }
        log = mock.Mock()
        with mock.patch.object(standalone_updater, "pip_install") as pip, \
                mock.patch.object(standalone_updater, "relaunch"):
            succeeded = standalone_updater.run_job(job, log)
        self.assertTrue(succeeded, log.error.call_args_list)
        return succeeded, pip

    def layout_head(self):
        with standalone.open_repo(self.repo) as repo:
            return repo.head()

    def add_app_files(self):
        write(os.path.join(self.working, "settings.json"), "{}")
        write(os.path.join(self.working, "configs", "user.json"), "{}")

    def assert_updated_with_app_files_kept(self):
        self.assertEqual("1.1.0\n", read(os.path.join(self.working, "app.txt")))
        self.assertEqual("side\n", read(os.path.join(self.working, "side.txt")))
        self.assertEqual("{}", read(os.path.join(self.working, "settings.json")))
        self.assertEqual("{}", read(os.path.join(self.working, "configs", "user.json")))
        self.assertTrue(standalone.is_bare_repo(self.repo))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "app.txt")), "repo keeps git data only")
        self.assertFalse(os.path.exists(os.path.join(self.working, ".git")))
        with standalone.open_repo(self.repo) as repo:
            self.assertEqual("v1.1.0", standalone.head_version(repo))

    def layout_tags(self):
        with standalone.open_repo(self.repo) as repo:
            return sorted(name.decode() for name in repo.refs.as_dict(standalone.TAG_PREFIX))

    def checkout_layout(self, version):
        git(self.root, "--git-dir", self.repo, "--work-tree", self.working, "checkout", "-q", "-f", version)

    def test_mirrors_tags_and_prunes_deleted(self):
        self.make_bare_layout()
        self.add_remote_release("v1.2.0", orphan=True)
        git(self.remote, "tag", "-d", "v1.1.0-beta")

        updated, pruned = standalone.fetch_tags(self.repo)

        self.assertEqual((["v1.2.0"], ["v1.1.0-beta"]), (updated, pruned))
        self.assertEqual(["v1.0.0", "v1.1.0", "v1.2.0"], self.layout_tags())

    def test_keep_releases_from_repo_config_fetches_only_kept_tags(self):
        self.make_bare_layout()
        git(self.root, "--git-dir", self.repo, "config", "pyappify.keepreleases", "1")
        self.add_remote_release("v1.2.0")
        self.add_remote_release("v1.3.0-beta")

        standalone.fetch_tags(self.repo)

        # v1.0.0 stays because it is checked out.
        self.assertEqual(["v1.0.0", "v1.2.0", "v1.3.0-beta"], self.layout_tags())

    def test_keep_releases_from_pyappify_yml_at_head(self):
        write(
            os.path.join(self.remote, "pyappify.yml"),
            "keep_releases: 1\n" + PYAPPIFY_YML,
        )
        self.add_remote_release("v1.2.0")
        self.make_bare_layout()
        self.checkout_layout("v1.2.0")

        standalone.fetch_tags(self.repo)

        self.assertEqual(["v1.2.0"], self.layout_tags())

    def test_finds_repo_from_inside_working(self):
        self.make_bare_layout()
        os.makedirs(os.path.join(self.working, "logs"))

        self.assertEqual(self.repo, standalone.find_repo_path(os.path.join(self.working, "logs"), environ={}))
        self.assertEqual("v1.0.0", standalone.detect_app_version(self.working))
        self.assertEqual(self.working, standalone.app_files_dir(self.repo))

    def test_update_checks_out_into_working_and_keeps_app_files(self):
        self.make_bare_layout()
        self.add_app_files()
        # Tracked files are forced to the new version, as the launcher does.
        write(os.path.join(self.working, "app.txt"), "edited by the app\n")

        succeeded, pip = self.run_update()

        self.assertTrue(succeeded)
        pip.assert_not_called()
        self.assert_updated_with_app_files_kept()

    def test_lists_only_tags(self):
        self.make_bare_layout()

        versions = standalone.list_versions(self.repo, 10, False)

        self.assertEqual(["tag"] * 3, [v["kind"] for v in versions])
        self.assertEqual("current", versions[-1]["relation"])
        self.assertFalse(standalone.is_plain_checkout(self.working))

    def test_update_request_uses_a_state_dir_outside_repo(self):
        self.make_bare_layout()
        write(os.path.join(self.working, "app.txt"), "edited by the app\n")
        with mock.patch.object(standalone, "find_repo_path", return_value=self.repo), \
                mock.patch.object(standalone, "fetch_tags", return_value=([], [])), \
                mock.patch.object(standalone, "_start_updater") as start:
            standalone.update_to_version("v1.1.0")

        job_path = start.call_args.args[0]
        self.assertEqual(os.path.join(self.app, "pyappify-update", "update-job.json"), job_path)
        self.assertEqual(self.repo, json.loads(read(job_path))["repo_path"])

    def test_first_update_converts_an_old_install(self):
        self.make_old_layout()
        self.add_app_files()
        self.assertEqual(self.repo, standalone.find_repo_path(self.working, environ={}))

        succeeded, _pip = self.run_update()

        self.assertTrue(succeeded)
        self.assert_updated_with_app_files_kept()
        self.assertFalse(os.path.exists(os.path.join(self.app, "repo.checkout.old")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "repo.git.migrating")))
        self.assertIn("bare = true", read(os.path.join(self.repo, "config")))

    def test_finishes_an_interrupted_conversion(self):
        self.make_old_layout()
        # Interrupted after the git data was moved out of repo.
        os.rename(os.path.join(self.repo, ".git"), os.path.join(self.app, "repo.git.migrating"))

        self.assertEqual(self.repo, standalone.find_repo_path(self.working, environ={}))
        self.assertFalse(os.path.exists(os.path.join(self.app, "repo.git.migrating")))
        self.assertEqual("v1.0.0", standalone.detect_app_version(self.working))

    def test_converts_and_updates_an_install_with_a_submodule(self):
        templates = os.path.join(self.root, "templates")
        os.makedirs(templates)
        git(templates, "init", "-q", "-b", "main")
        write(os.path.join(templates, "template.txt"), "t1\n")
        git(templates, "add", "-A")
        git(templates, "commit", "-q", "-m", "templates")
        git(self.remote, "-c", "protocol.file.allow=always", "submodule", "add", "-q", templates, "ok_templates")
        self.commit("Add templates")
        git(self.remote, "tag", "v1.2.0")
        self.commit_file("app.txt", "1.3.0\n", "Release 1.3.0")
        git(self.remote, "tag", "v1.3.0")

        git(self.root, "clone", "-q", self.remote, self.repo)
        git(self.repo, "checkout", "-q", "v1.2.0")
        git(self.repo, "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init")
        # Dulwich, like git, refuses file:// submodule URLs unless allowed.
        git(self.repo, "config", "protocol.file.allow", "always")
        copy_without_git(self.repo, self.working)
        self.add_app_files()

        succeeded, _pip = self.run_update("v1.3.0")

        self.assertTrue(succeeded)
        self.assertEqual("1.3.0\n", read(os.path.join(self.working, "app.txt")))
        self.assertEqual("t1\n", read(os.path.join(self.working, "ok_templates", "template.txt")))
        self.assertEqual(
            "gitdir: ../../repo/modules/ok_templates",
            read(os.path.join(self.working, "ok_templates", ".git")).strip(),
        )
        self.assertEqual("{}", read(os.path.join(self.working, "configs", "user.json")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "ok_templates")))


class TestStandalonePipIndex(unittest.TestCase):
    def test_invalid_file_uses_defaults_and_warns_during_install(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            standalone, "find_repo_path", return_value=directory
        ), mock.patch.object(standalone, "state_dir", return_value=directory), mock.patch.object(
            pyappify.updates, "_is_python_test", return_value=False
        ), mock.patch.object(pyappify.updates, "uses_launcher", return_value=False):
            process = mock.Mock(stdout=[])
            process.wait.return_value = 0
            for content in ('{bad json', '[]', '{}', '{"pip_index_url": 42}',
                            '{"pip_index_url": "https://unknown.invalid/"}'):
                with self.subTest(content=content):
                    write(os.path.join(directory, "pip-index.json"), content)
                    self.assertEqual("", pyappify.get_pip_index_config()["pip_index_url"])
                    log = mock.Mock()
                    with mock.patch.object(standalone_updater.subprocess, "Popen", return_value=process) as popen:
                        standalone_updater.pip_install(directory, "demo", "--no-deps", log)
                    self.assertNotIn("--index-url", popen.call_args.args[0])
                    process.wait.assert_called()
                    log.warning.assert_called_once()
                    pyappify.set_pip_index_url(pyappify.PIP_INDEX_URLS[1])
                    self.assertEqual(pyappify.PIP_INDEX_URLS[1], pyappify.get_pip_index_config()["pip_index_url"])

    def test_persistence_and_pip_arguments(self):
        from pyappify import pip_index
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            standalone, "find_repo_path", return_value=directory
        ), mock.patch.object(standalone, "state_dir", return_value=directory), mock.patch.object(
            pyappify.updates, "_is_python_test", return_value=False
        ), mock.patch.object(pyappify.updates, "uses_launcher", return_value=False):
            self.assertEqual("", pyappify.get_pip_index_config()["pip_index_url"])
            with self.assertRaises(ValueError):
                pyappify.set_pip_index_url("https://unknown.invalid/")
            with self.assertRaises(TypeError):
                pyappify.set_pip_index_url(None)
            selected = pyappify.PIP_INDEX_URLS[2]
            pyappify.set_pip_index_url(selected)
            self.assertEqual(selected, json.loads(read(os.path.join(directory, "pip-index.json")))["pip_index_url"])
            self.assertEqual(selected, pyappify.get_pip_index_config()["pip_index_url"])
            process = mock.Mock(stdout=[])
            process.wait.return_value = 0
            for url in (selected, ""):
                pyappify.set_pip_index_url(url)
                for args in ("--no-deps", "--index-url https://profile/", "-i https://profile/",
                             "--index-url=https://profile/", "-ihttps://profile/"):
                    with mock.patch.object(standalone_updater.subprocess, "Popen", return_value=process) as popen:
                        standalone_updater.pip_install(directory, "demo", args, mock.Mock())
                    command = popen.call_args.args[0]
                    self.assertEqual(bool(url) and args == "--no-deps", selected in command)
                    self.assertEqual(args == "--no-deps" and bool(url), "--index-url" in command and selected in command)
                    self.assertIn("demo", command)
            self.assertEqual("", pip_index.read_url(directory))


if __name__ == "__main__":
    unittest.main()
