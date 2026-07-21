import json
import os
import tempfile
import threading
import unittest

from pyappify.app_config import (
    AppConfigAPI,
    UPDATE_METHOD_AUTO_PRE_RELEASE,
    UPDATE_METHOD_MANUAL,
)


class TestAppConfig(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temporary_directory.name, "app.json")
        self._write_document(
            {
                "name": "example",
                "installed": True,
                "auto_start": False,
                "update_method": "AUTO_UPDATE",
            }
        )
        self.api = AppConfigAPI(self.config_path)

    def tearDown(self):
        self.api.stop_watcher()
        self.temporary_directory.cleanup()

    def _write_document(self, document):
        with open(self.config_path, "w", encoding="utf-8") as config_file:
            json.dump(document, config_file)

    def test_reads_and_updates_preferences_without_losing_other_fields(self):
        self.assertEqual(
            {"auto_start": False, "update_method": "AUTO_UPDATE"},
            self.api.get(),
        )

        self.api.update(
            auto_start=True,
            update_method=UPDATE_METHOD_AUTO_PRE_RELEASE,
        )

        with open(self.config_path, "r", encoding="utf-8") as config_file:
            saved = json.load(config_file)
        self.assertEqual("example", saved["name"])
        self.assertTrue(saved["installed"])
        self.assertTrue(saved["auto_start"])
        self.assertEqual(UPDATE_METHOD_AUTO_PRE_RELEASE, saved["update_method"])

    def test_rejects_invalid_preferences(self):
        with self.assertRaises(ValueError):
            self.api.update(auto_start="yes")
        with self.assertRaises(ValueError):
            self.api.update(update_method="UNKNOWN")
        with self.assertRaises(ValueError):
            self.api.update(update_method=[])

    def test_watcher_notifies_when_an_external_writer_changes_the_file(self):
        changed = threading.Event()
        received = []

        def listener(config):
            received.append(config)
            changed.set()

        self.api.add_listener(listener)
        self.api.configure(self.config_path, watch=True, watch_interval=0.05)
        self._write_document(
            {
                "name": "example",
                "installed": True,
                "auto_start": True,
                "update_method": UPDATE_METHOD_MANUAL,
            }
        )

        self.assertTrue(changed.wait(2), "watcher did not observe app.json change")
        self.assertEqual(
            {"auto_start": True, "update_method": UPDATE_METHOD_MANUAL},
            received[-1],
        )


if __name__ == "__main__":
    unittest.main()
