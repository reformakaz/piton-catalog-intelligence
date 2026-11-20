import unittest

from scan_v6 import TABLES, header_status


class ScanHeaderTest(unittest.TestCase):
    def test_page_63_recovers_only_missing_first_nominal(self):
        ratings = TABLES[63][1]
        self.assertEqual(header_status(63, ratings, list(map(float, ratings))), 'ok')
        self.assertEqual(
            header_status(63, ratings, [None, 3200.0, 4000.0, 5000.0, 6400.0]),
            'recovered_2500_from_template',
        )
        for page, recognized in (
            (63, [2800, 3200, 4000, 5000, 6400]),
            (63, [None, None, 4000, 5000, 6400]),
            (63, [2500, None, 4000, 5000, 6400]),
            (63, [None, 4000, 3200, 5000, 6400]),
            (62, [None, 3200, 4000, 5000, 6400]),
        ):
            with self.subTest(page=page, recognized=recognized):
                self.assertEqual(header_status(page, ratings, recognized), 'mismatch')
