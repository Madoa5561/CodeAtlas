"""Package metadata tests."""

import unittest

import codeatlas


class PackageTest(unittest.TestCase):
    """Verify the minimal package can be imported."""

    def test_version_is_exposed(self) -> None:
        self.assertEqual(codeatlas.__version__, "0.0.0")


if __name__ == "__main__":
    unittest.main()
