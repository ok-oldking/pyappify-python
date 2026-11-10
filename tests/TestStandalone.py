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

    def local_tags(self):
        with standalone.open_repo(self.clone) as repo:
            return sorted(name.decode() for name in repo.refs.as_dict(standalone.TAG_PREFIX))

    def test_keep_releases_from_repo_config_fetches_only_kept_tags(self):
        git(self.clone, "config", "pyappify.keepreleases", "1")
        self.add_remote_release("v1.2.0")
        self.add_remote_release("v1.3.0-beta")

        standalone.fetch_tags(self.clone)

        # v1.0.0 stays because it is checked out.
        self.assertEqual(["v1.0.0", "v1.2.0", "v1.3.0-beta"], self.local_tags())

    def test_keep_releases_from_pyappify_yml_at_head(self):
        write(
            os.path.join(self.remote, "pyappify.yml"),
            "keep_releases: 1\n" + PYAPPIFY_YML,
        )
        self.add_remote_release("v1.2.0")
        git(self.clone, "fetch", "-q", "--tags")
        git(self.clone, "checkout", "-q", "v1.2.0")

        standalone.fetch_tags(self.clone)

        self.assertEqual(["v1.2.0"], self.local_tags())

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


if __name__ == "__main__":
    unittest.main()
