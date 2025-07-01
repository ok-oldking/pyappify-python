# filename: test_pyappify.is_greater_version.py
import unittest
import pyappify

class TestIsGreaterVersion(unittest.TestCase):

    def test_version1_is_greater(self):
        self.assertTrue(pyappify.is_greater_version("2.0.0", "1.9.9"))
        self.assertTrue(pyappify.is_greater_version("1.2.0", "v1.1.9"))
        self.assertTrue(pyappify.is_greater_version("v1.1.2", "1.1.1"))
        self.assertTrue(pyappify.is_greater_version("1.1.0", "1.1"))
        self.assertTrue(pyappify.is_greater_version("v10.0.0", "9.0.0"))

    def test_version1_is_not_greater(self):
        self.assertFalse(pyappify.is_greater_version("v1.0.0", "2.0.0"))
        self.assertFalse(pyappify.is_greater_version("1.1.0", "v1.2.0"))
        self.assertFalse(pyappify.is_greater_version("1.1.1", "1.1.2"))
        self.assertFalse(pyappify.is_greater_version("1.1", "1.1.0"))

    def test_versions_are_equal(self):
        self.assertFalse(pyappify.is_greater_version("qwer", "1.0.0"))
        self.assertFalse(pyappify.is_greater_version("1.2.3", "1.2.3"))

    def test_invalid_or_edge_cases(self):
        self.assertFalse(pyappify.is_greater_version(None, "1.0.0"))
        self.assertFalse(pyappify.is_greater_version("1.0.0", None))
        self.assertFalse(pyappify.is_greater_version(None, None))
        self.assertFalse(pyappify.is_greater_version("v1.a.0", "1.0.0"))
        self.assertFalse(pyappify.is_greater_version("1.0.0", "invalid"))
        self.assertFalse(pyappify.is_greater_version("", "1.0.0"))

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)