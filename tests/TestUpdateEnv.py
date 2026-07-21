import importlib
import os
import unittest

import pyappify


ENV_KEYS = (
    "PYAPPIFY_APP_VERSION",
    "PYAPPIFY_APP_STARTING_VERSION",
    "PYAPPIFY_UPDATE_NOTE",
    "PYAPPIFY_APP_JSON_PATH",
    "PYAPPIFY_LOCALE",
)


class TestUpdateEnv(unittest.TestCase):
    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(pyappify)

    def _reload_with_env(self, **env):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        for key, value in env.items():
            os.environ[key] = value
        return importlib.reload(pyappify)

    def test_starting_version_falls_back_to_app_version(self):
        module = self._reload_with_env(PYAPPIFY_APP_VERSION="1.2.3")

        self.assertEqual("1.2.3", module.app_version)
        self.assertEqual("1.2.3", module.app_starting_version)
        self.assertFalse(module.is_app_updated())
        self.assertFalse(module.is_updated())
        self.assertFalse(module.is_app_downgraded())
        self.assertFalse(module.is_downgrade())

    def test_missing_versions_are_not_update_or_downgrade(self):
        module = self._reload_with_env()

        self.assertIsNone(module.app_version)
        self.assertIsNone(module.app_starting_version)
        self.assertFalse(module.is_app_updated())
        self.assertFalse(module.is_app_downgraded())

    def test_detects_update_and_downgrade(self):
        module = self._reload_with_env(
            PYAPPIFY_APP_VERSION="2.0.0",
            PYAPPIFY_APP_STARTING_VERSION="1.0.0",
        )

        self.assertTrue(module.is_app_updated())
        self.assertTrue(module.is_updated())
        self.assertFalse(module.is_app_downgraded())
        self.assertFalse(module.is_downgrade())

        module = self._reload_with_env(
            PYAPPIFY_APP_VERSION="1.0.0",
            PYAPPIFY_APP_STARTING_VERSION="2.0.0",
        )

        self.assertFalse(module.is_app_updated())
        self.assertFalse(module.is_updated())
        self.assertTrue(module.is_app_downgraded())
        self.assertTrue(module.is_downgrade())

    def test_returns_update_notes_from_json_array(self):
        module = self._reload_with_env(
            PYAPPIFY_UPDATE_NOTE='["first change", "second change"]'
        )

        self.assertEqual(["first change", "second change"], module.get_update_notes())
        self.assertEqual(["first change", "second change"], module.get_update_note())

    def test_invalid_or_missing_update_notes_return_empty_list(self):
        self.assertEqual([], self._reload_with_env().get_update_notes())
        self.assertEqual(
            [],
            self._reload_with_env(PYAPPIFY_UPDATE_NOTE="not json").get_update_notes(),
        )
        self.assertEqual(
            [],
            self._reload_with_env(PYAPPIFY_UPDATE_NOTE='"single note"').get_update_notes(),
        )

    def test_reads_locale_with_english_fallback(self):
        self.assertEqual("zh-CN", self._reload_with_env(PYAPPIFY_LOCALE="zh-CN").get_locale())
        self.assertEqual("en", self._reload_with_env().get_locale())


if __name__ == "__main__":
    unittest.main()
