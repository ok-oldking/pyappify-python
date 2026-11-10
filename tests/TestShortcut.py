import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pyappify
from pyappify import shortcut, standalone_updater


def read_shortcut(path):
    """Read a .lnk back through WScript.Shell, plus its "Run as administrator" bit."""
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
        "@{target=$s.TargetPath; arguments=$s.Arguments; working_dir=$s.WorkingDirectory; "
        "icon=$s.IconLocation; description=$s.Description} | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [shortcut._powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(os.environ, LNK=path), capture_output=True, encoding="utf-8", check=True,
    )
    details = json.loads(completed.stdout)
    with open(path, "rb") as link_file:
        link_file.seek(0x14)
        details["run_as_admin"] = bool(int.from_bytes(link_file.read(4), "little") & 0x2000)
    return details


@unittest.skipUnless(os.name == "nt", "Windows shortcuts")
class TestCreateShortcut(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.working = os.path.join(self.root, "My App")
        self.out = os.path.join(self.root, "Start Menu")
        os.makedirs(os.path.join(self.working, "icons"))

    def write_yml(self, text):
        with open(os.path.join(self.working, "pyappify.yml"), "w", encoding="utf-8") as file:
            file.write(text)

    def test_explicit_values_and_unicode(self):
        icon = os.path.join(self.working, "icons", "app.ico")
        open(icon, "wb").close()

        paths = pyappify.create_shortcut(
            name='Démo: "App"',
            target=sys.executable,
            arguments=["main.py", "--profile", "two words"],
            working_dir=self.working,
            icon=icon,
            run_as_admin=True,
            directories=[self.out],
        )

        self.assertEqual([os.path.join(self.out, "Démo_ _App_.lnk")], paths)
        details = read_shortcut(paths[0])
        self.assertEqual(os.path.normcase(sys.executable), os.path.normcase(details["target"]))
        self.assertEqual('main.py --profile "two words"', details["arguments"])
        self.assertEqual(self.working, details["working_dir"])
        self.assertEqual(icon + ",0", details["icon"])
        self.assertEqual('Démo_ _App_', details["description"])
        self.assertTrue(details["run_as_admin"])

    def test_defaults_come_from_pyappify_yml_and_current_command(self):
        self.write_yml(
            'name: "ok-demo"\n'
            'icon: "icons/icon.png"\n'
            "profiles:\n"
            '  - name: "China"\n'
            "    admin: true\n"
            '  - name: "Global"\n'
            '    main_script: "main.py"\n'
        )
        open(os.path.join(self.working, "icons", "icon.ico"), "wb").close()

        with mock.patch.object(pyappify, "app_profile", "Global"), mock.patch.object(
            shortcut.standalone, "_relaunch_argv",
            return_value=[sys.executable, "main.py", "--start"],
        ):
            paths = pyappify.create_shortcut(working_dir=self.working, directories=[self.out])

        details = read_shortcut(paths[0])
        self.assertEqual(os.path.join(self.out, "ok-demo.lnk"), paths[0])
        self.assertEqual("main.py --start", details["arguments"])
        self.assertEqual(os.path.join(self.working, "icons", "icon.ico") + ",0", details["icon"])
        self.assertTrue(details["run_as_admin"], "Global inherits admin from the first profile")

    def test_updating_clears_admin_and_ignores_icons_outside_working_dir(self):
        self.write_yml('icon: "../outside.ico"\n')
        open(os.path.join(self.root, "outside.ico"), "wb").close()
        kwargs = dict(target=sys.executable, arguments="", working_dir=self.working,
                      directories=[self.out])

        pyappify.create_shortcut(run_as_admin=True, **kwargs)
        path = pyappify.create_shortcut(run_as_admin=None, **kwargs)[0]

        details = read_shortcut(path)
        self.assertEqual(os.path.join(self.out, "My App.lnk"), path)
        self.assertFalse(details["run_as_admin"])
        self.assertNotIn("outside", details["icon"])

    def test_returns_only_shortcuts_created_or_changed(self):
        icon = os.path.join(self.working, "icons", "app.ico")
        open(icon, "wb").close()
        kwargs = dict(name="Demo", target=sys.executable, arguments=["main.py"],
                      working_dir=self.working, icon=icon, run_as_admin=False)
        other = os.path.join(self.root, "Desktop")
        path = os.path.join(self.out, "Demo.lnk")

        self.assertEqual([path], pyappify.create_shortcut(directories=[self.out], **kwargs))
        before = os.stat(path).st_mtime_ns
        self.assertEqual([], pyappify.create_shortcut(directories=[self.out], **kwargs))
        self.assertEqual(before, os.stat(path).st_mtime_ns, "an up-to-date shortcut is not rewritten")

        # A new location is created while the up-to-date one is skipped.
        self.assertEqual(
            [os.path.join(other, "Demo.lnk")],
            pyappify.create_shortcut(directories=[self.out, other], **kwargs),
        )
        for change in (dict(arguments=["main.py", "--x"]), dict(icon=""), dict(run_as_admin=True),
                       dict(working_dir=self.root), dict(target=os.path.join(self.root, "x.exe"))):
            with self.subTest(change=change):
                self.assertEqual(
                    [path], pyappify.create_shortcut(directories=[self.out], **dict(kwargs, **change))
                )
                pyappify.create_shortcut(directories=[self.out], **kwargs)
        self.assertEqual("", read_shortcut(path)["arguments"].replace("main.py", ""))

    def test_disabled_locations_are_not_created_or_updated(self):
        kwargs = dict(name="Demo", target=sys.executable, arguments="", working_dir=self.working)
        with self.fake_known_folders():
            self.assertEqual([], pyappify.create_shortcut(start_menu=False, desktop=False, **kwargs))
            only_desktop = pyappify.create_shortcut(start_menu=False, **kwargs)
        self.assertEqual([os.path.join(self.root, "Desktop", "Demo.lnk")], only_desktop)
        self.assertFalse(os.path.exists(os.path.join(self.root, "Programs")))

    def fake_known_folders(self):
        folders = {
            shortcut._CSIDL_PROGRAMS: os.path.join(self.root, "Programs"),
            shortcut._CSIDL_DESKTOPDIRECTORY: os.path.join(self.root, "Desktop"),
        }
        return mock.patch.object(shortcut, "_known_folder", side_effect=folders.__getitem__)

    def test_refresh_writes_start_menu_and_only_an_existing_desktop_shortcut(self):
        self.write_yml('name: "ok-demo"\n')
        kwargs = dict(target=sys.executable, arguments=["main.py"], working_dir=self.working)
        desktop_link = os.path.join(self.root, "Desktop", "ok-demo.lnk")

        with self.fake_known_folders():
            first = pyappify.refresh_shortcuts(**kwargs)
            self.assertEqual([os.path.join(self.root, "Programs", "ok-demo.lnk")], first)
            self.assertFalse(os.path.exists(desktop_link))

            pyappify.create_shortcut(desktop=True, start_menu=False, **kwargs)
            second = pyappify.refresh_shortcuts(
                target=sys.executable, arguments=["main.py", "--v2"], working_dir=self.working
            )

        self.assertEqual(first + [desktop_link], second)
        self.assertEqual("main.py --v2", read_shortcut(desktop_link)["arguments"])
        with self.fake_known_folders():
            self.assertEqual([], pyappify.refresh_shortcuts(
                target=sys.executable, arguments=["main.py", "--v2"], working_dir=self.working
            ))

    def test_updater_refreshes_shortcuts_with_the_job_command_and_profile(self):
        self.write_yml(
            "profiles:\n"
            '  - name: "China"\n'
            '  - name: "Global"\n'
            "    admin: true\n"
        )
        job = {
            "repo_path": self.working,
            "profile": "Global",
            "relaunch": {"argv": [sys.executable, "main.py"], "cwd": self.working},
            "refresh_shortcuts": True,
        }
        with self.fake_known_folders():
            paths = standalone_updater.refresh_shortcuts(job, mock.Mock())
            skipped = standalone_updater.refresh_shortcuts(
                dict(job, refresh_shortcuts=False), mock.Mock()
            )

        self.assertEqual([os.path.join(self.root, "Programs", "My App.lnk")], paths)
        self.assertEqual([], skipped)
        details = read_shortcut(paths[0])
        self.assertEqual("main.py", details["arguments"])
        self.assertTrue(details["run_as_admin"])

    def test_updater_only_logs_shortcut_failures(self):
        job = {"repo_path": self.working, "relaunch": {"argv": [sys.executable]},
               "refresh_shortcuts": True}
        log = mock.Mock()
        with mock.patch.object(shortcut, "refresh_shortcuts", side_effect=OSError("boom")):
            self.assertEqual([], standalone_updater.refresh_shortcuts(job, log))
        log.warning.assert_called_once()

    def test_default_locations_are_start_menu_and_desktop(self):
        with mock.patch.object(shortcut, "_known_folder", side_effect=lambda csidl: str(csidl)):
            self.assertEqual(["2", "16"], shortcut._default_directories(True, True))
            self.assertEqual(["2"], shortcut._default_directories(True, False))
        programs = shortcut._known_folder(shortcut._CSIDL_PROGRAMS)
        self.assertTrue(programs.endswith(os.path.join("Start Menu", "Programs")))


class TestCreateShortcutPlatform(unittest.TestCase):
    def test_other_platforms_raise(self):
        with mock.patch.object(shortcut.os, "name", "posix"):
            with self.assertRaisesRegex(OSError, "only be created on Windows"):
                shortcut.create_shortcut()


if __name__ == "__main__":
    unittest.main()
