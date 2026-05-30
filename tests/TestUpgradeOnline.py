import os
import unittest

from tests.TestUpgrade import assert_upgrade_kills_running_executable_and_replaces_it


class TestUpgradeOnline(unittest.TestCase):
    ZIP_URL = (
        "https://github.com/ok-oldking/ok-wuthering-waves/"
        "releases/download/v3.3.46/ok-ww-win32.zip"
    )

    @unittest.skipUnless(os.name == "nt", "executable replacement test is Windows-only")
    def test_upgrade_from_online_zip_kills_running_executable_and_replaces_it(self):
        assert_upgrade_kills_running_executable_and_replaces_it(
            self, self.ZIP_URL, timeout_seconds=600
        )


if __name__ == "__main__":
    unittest.main()
