import json
import os
import sys
import time
import unittest
from unittest import mock

from TestStandalone import StandaloneRepoTestCase, read, write

from pyappify import i18n, pip_index, standalone, standalone_updater, updater_ui

try:
    import tkinter

    from pyappify import _tk_updater
except ImportError:  # a Python without Tk
    tkinter = None
    _tk_updater = None


class TestCanShowWindow(unittest.TestCase):
    def test_console_choice_wins(self):
        self.assertFalse(updater_ui.can_show_window({"PYAPPIFY_UPDATER_UI": "console"}))

    def test_needs_tkinter(self):
        with mock.patch.object(updater_ui.importlib.util, "find_spec", return_value=None):
            self.assertFalse(updater_ui.can_show_window({}))
            self.assertFalse(updater_ui.can_show_window({"PYAPPIFY_UPDATER_UI": "tk"}))

    def test_linux_needs_a_display(self):
        with mock.patch.object(updater_ui.importlib.util, "find_spec", return_value=object()), \
                mock.patch.object(updater_ui.os, "name", "posix"), \
                mock.patch.object(updater_ui.sys, "platform", "linux"):
            self.assertFalse(updater_ui.can_show_window({}))
            self.assertTrue(updater_ui.can_show_window({"DISPLAY": ":0"}))
            self.assertTrue(updater_ui.can_show_window({"PYAPPIFY_UPDATER_UI": "tk"}))


class TestProgressReporting(StandaloneRepoTestCase):
    def job(self, version):
        with standalone.open_repo(self.clone) as repo:
            head = repo.head().decode()
        return {
            "repo_path": self.clone,
            "version": version,
            "previous_version": "v1.0.0",
            "previous_commit": head,
            "update_note": [],
            "profile": "Global",
            "pid": None,
            "relaunch": {"argv": None, "cwd": self.clone, "pythonpath": None},
        }

    def run_job(self, job):
        events = []
        with mock.patch.object(standalone_updater, "pip_install"), \
                mock.patch.object(standalone_updater, "relaunch", return_value=None):
            succeeded = standalone_updater.run_job(
                job, mock.Mock(), lambda step, status, detail=None: events.append((step, status))
            )
        return succeeded, events

    def test_reports_each_step(self):
        succeeded, events = self.run_job(self.job("v1.1.0"))

        self.assertTrue(succeeded)
        self.assertEqual([
            ("stop", "active"), ("stop", "done"),
            ("checkout", "active"), ("checkout", "done"),
            ("dependencies", "skipped"),
            ("restart", "active"), ("restart", "skipped"),
        ], events)

    def test_reports_the_failed_step_and_rollback(self):
        succeeded, events = self.run_job(self.job("v9.9.9"))

        self.assertFalse(succeeded)
        self.assertIn(("checkout", "failed"), events)
        self.assertEqual([("rollback", "active"), ("rollback", "done")], events[-2:])

    def test_pip_output_becomes_dependency_detail(self):
        lines = []
        process = mock.Mock(returncode=0, stdout=iter([
            "Collecting six==1.16.0\n", "  some noise\n", "Successfully installed six\n",
        ]))
        process.wait.return_value = 0
        with mock.patch.object(standalone_updater.subprocess, "Popen", return_value=process):
            standalone_updater.pip_install(self.clone, "requirements.txt", "", mock.Mock(),
                                           on_line=lines.append)
        self.assertEqual(["Collecting six==1.16.0", "Successfully installed six"], lines)


class TestUpdateJobs(StandaloneRepoTestCase):
    def test_prepared_job_without_app_neither_stops_nor_restarts(self):
        job = standalone.prepare_update_job("v1.1.0", repo_path=self.clone)

        self.assertIsNone(job["pid"])
        self.assertIsNone(job["relaunch"]["argv"])
        self.assertFalse(job["refresh_shortcuts"])
        self.assertIn("Release 1.1.0", job["update_note"])

    def start_updater(self, show_window):
        with mock.patch.object(updater_ui, "can_show_window", return_value=show_window), \
                mock.patch.object(standalone.subprocess, "Popen") as popen:
            standalone._start_updater(os.path.join(self.root, "update-job.json"))
        return popen.call_args.args[0]

    def test_starts_the_window_when_tk_is_available(self):
        command = self.start_updater(True)
        self.assertEqual(["-m", "pyappify.updater_ui", "--job"], command[1:4])

    def test_starts_the_console_updater_without_tk(self):
        command = self.start_updater(False)
        self.assertEqual(["-m", "pyappify.standalone_updater"], command[1:3])

    def test_open_updater_writes_a_session(self):
        with mock.patch.object(updater_ui, "can_show_window", return_value=True), \
                mock.patch.object(updater_ui, "_spawn") as spawn:
            pyappify_session = updater_ui.open_updater(app_version="v1.0.0")

        command = spawn.call_args.args[0]
        self.assertIs(spawn.return_value, pyappify_session)
        session = json.loads(read(command[-1]))
        self.assertEqual(self.clone, session["repo_path"])
        self.assertEqual(os.getpid(), session["pid"])
        self.assertEqual("v1.0.0", session["app_version"])
        self.assertEqual(sys.executable, session["relaunch"]["argv"][0])


class TestI18n(unittest.TestCase):
    def test_languages_match_the_launcher(self):
        self.assertEqual(("en", "zh-CN", "zh-TW", "es", "ja", "ko"), i18n.LANGUAGES)

    def test_every_text_is_translated_with_the_same_placeholders(self):
        import string

        def fields(text):
            return {name for _, name, _, _ in string.Formatter().parse(text) if name}

        for key, texts in i18n.STRINGS.items():
            self.assertEqual(len(i18n.LANGUAGES), len(texts), key)
            for language, text in zip(i18n.LANGUAGES, texts):
                self.assertTrue(text, (key, language))
                self.assertEqual(fields(texts[0]), fields(text), (key, language))

    def test_locale_mapping_follows_the_launcher(self):
        cases = {
            "zh-CN": "zh-CN", "zh_CN.UTF-8": "zh-CN", "zh-Hans-CN": "zh-CN",
            "zh-TW": "zh-TW", "zh_HK": "zh-TW", "zh-Hant-TW": "zh-TW",
            "es-MX": "es", "es": "es", "ja_JP": "ja", "ko-KR": "ko",
            "en-US": "en", "fr-FR": "en", "": "en", None: "en",
        }
        for locale, language in cases.items():
            self.assertEqual(language, i18n.language_from_locale(locale), locale)

    def test_choice_then_launcher_then_os(self):
        with mock.patch.object(i18n, "system_locale", return_value="ko-KR"):
            self.assertEqual("ja", i18n.resolve_language("ja", {"PYAPPIFY_LOCALE": "es"}))
            self.assertEqual("es", i18n.resolve_language(None, {"PYAPPIFY_LOCALE": "es"}))
            self.assertEqual("zh-TW", i18n.resolve_language("bogus", {"PYAPPIFY_LOCALE": "zh_tw"}))
            self.assertEqual("ko", i18n.resolve_language(None, {}))

    def test_missing_translation_falls_back(self):
        self.assertEqual("Closing in 3s…", i18n.t("closing_in", "fr", seconds=3))
        self.assertEqual("no_such_key", i18n.t("no_such_key", "ja"))


@unittest.skipIf(_tk_updater is None, "tkinter is not available")
class TestUpdaterWindow(StandaloneRepoTestCase):
    def setUp(self):
        super().setUp()
        try:
            self.tk_root = tkinter.Tk()
        except tkinter.TclError as error:
            self.skipTest("no display: {}".format(error))
        self.tk_root.withdraw()
        self.addCleanup(self.destroy)
        preferences = os.path.join(self.root, "updater-ui.json")
        for patcher in (
            mock.patch.object(updater_ui, "_preferences_path", return_value=preferences),
            mock.patch.dict(os.environ, {"PYAPPIFY_LOCALE": "en"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.preferences = preferences

    def destroy(self):
        try:
            for pending in self.tk_root.tk.splitlist(self.tk_root.tk.call("after", "info")):
                self.tk_root.after_cancel(pending)
            self.tk_root.destroy()
        except tkinter.TclError:
            pass

    def pump(self, until, timeout=30):
        deadline = time.monotonic() + timeout
        while not until():
            if time.monotonic() > deadline:
                self.fail("timed out waiting for the window")
            self.tk_root.update()
            time.sleep(0.02)

    def test_manager_lists_versions_and_saves_the_mirror(self):
        window = _tk_updater.UpdaterWindow(self.tk_root, session={"repo_path": self.clone})
        window.start_manager()
        self.pump(lambda: window.versions and window.busy == 0)

        self.assertEqual(["v1.1.0", "v1.0.0"], [v["version"] for v in window.versions])
        self.assertEqual("Update to v1.1.0", window.update_button.cget("text"))
        self.assertIn("Release 1.1.0", window.notes.get("1.0", "end"))

        window.mirror_box.current(2)
        window._on_mirror_selected()
        self.pump(lambda: window.mirror_saved.cget("text"))
        self.assertEqual(pip_index.PIP_INDEX_URLS[2], pip_index.read_url(self.clone))

        window.release_only.set(False)
        window.refresh_versions()
        self.pump(lambda: len(window.versions) > 2 and window.busy == 0)
        self.assertIn("v1.1.0-beta", [v["version"] for v in window.versions])

    def test_language_switches_live_and_is_remembered(self):
        window = _tk_updater.UpdaterWindow(self.tk_root, session={"repo_path": self.clone})
        window.start_manager()
        self.pump(lambda: window.versions and window.busy == 0)

        window.set_language(i18n.JA)
        self.tk_root.update()

        self.assertEqual("v1.1.0 にアップデート", window.update_button.cget("text"))
        self.assertEqual("バージョン", window.tree.heading("version", "text"))
        self.assertIn("v1.0.0 からの変更点", window.notes.get("1.0", "end"))
        self.assertEqual("日本語", window.language_button.cget("text"))
        self.assertEqual(i18n.JA, json.loads(read(self.preferences))["language"])
        if "Yu Gothic UI" in window.theme.families:
            self.assertEqual("Yu Gothic UI", window.theme.fonts["body"].cget("family"))

        reopened = _tk_updater.UpdaterWindow(tkinter.Toplevel(self.tk_root),
                                             session={"repo_path": self.clone})
        self.assertEqual(i18n.JA, reopened.language)

    def test_themes_switch_and_are_remembered(self):
        window = _tk_updater.UpdaterWindow(self.tk_root, session={"repo_path": self.clone})
        for _ in _tk_updater.THEME_CYCLE:
            window.cycle_theme()
            self.tk_root.update()
            expected = _tk_updater.PALETTES[window.theme.resolved]["surface"]
            self.assertEqual(expected, window.notes.cget("background"))
        self.assertEqual(window.theme.mode, json.loads(read(self.preferences))["theme"])

    def test_job_window_applies_the_update(self):
        job = standalone.prepare_update_job("v1.1.0", repo_path=self.clone)
        job_path = standalone.write_update_job(job)
        window = _tk_updater.UpdaterWindow(self.tk_root, job=job, job_path=job_path)
        with mock.patch.object(standalone_updater, "pip_install"):
            window.start_job()
            self.pump(lambda: not window.updating)

        self.assertEqual("Updated to v1.1.0", window.progress_title.cget("text"))
        self.assertEqual(
            {"stop": "done", "checkout": "done", "dependencies": "skipped", "restart": "skipped"},
            {key: window.steps[key].status for key in standalone_updater.STEPS},
        )
        self.assertIn("Updated to v1.1.0.", window.log_text.get("1.0", "end"))
        self.assertFalse(os.path.exists(job_path))
        if window.close_after:
            self.tk_root.after_cancel(window.close_after)

    def test_job_window_shows_failures(self):
        job = standalone.prepare_update_job("v1.1.0", repo_path=self.clone)
        job["version"] = "v9.9.9"
        window = _tk_updater.UpdaterWindow(
            self.tk_root, job=job, job_path=standalone.write_update_job(job))
        window.start_job()
        self.pump(lambda: not window.updating)

        self.assertEqual("Could not update to v9.9.9", window.progress_title.cget("text"))
        self.assertEqual("failed", window.steps["checkout"].status)
        self.assertEqual("done", window.steps["rollback"].status)
        self.assertIn("v9.9.9", window.banner_text.cget("text"))


if __name__ == "__main__":
    unittest.main()
