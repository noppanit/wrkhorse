import unittest

from adder.mathy import add


class AddTests(unittest.TestCase):
    def test_two_plus_three(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
