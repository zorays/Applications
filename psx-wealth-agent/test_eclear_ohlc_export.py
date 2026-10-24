import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from eclear_ohlc_export import export, parse_line


class EClearNativeTableTests(unittest.TestCase):
    def test_trusted_table_dates_prices_volume(self):
        r = parse_line('Oct 23, 2026\t61.39\t61.99\t60.00\t61.39\t0.00\t287')
        self.assertEqual(r['date'], '2026-10-23')
        self.assertEqual(r['volume'], 287)
        self.assertEqual(r['close'], '61.39')

    def test_zero_and_impossible_ohlc_quarantined(self):
        with self.assertRaisesRegex(ValueError, 'impossible_ohlc_relationship'):
            parse_line('Sep 28, 2026\t64.50\t64.50\t64.50\t62.92\t0.00\t1')
        with self.assertRaisesRegex(ValueError, 'weekend_nontrading_session'):
            parse_line('Nov 22, 2025\t0\t0\t0\t0\t0\t0')
        with self.assertRaisesRegex(ValueError, 'zero_or_negative_price'):
            parse_line('May 08, 2026\t0\t0\t0\t0\t0\t0')

    def test_251_valid_trading_sessions(self):
        days = []
        d = date.today()
        while len(days) < 251:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        clipboard = '\n'.join(
            f'{x.strftime("%b %d, %Y")}\t60\t61\t59\t60.50\t0.5\t1,200'
            for x in days
        )
        with tempfile.TemporaryDirectory() as root:
            out, audit, rejects, info = export('BNWM', clipboard, Path(root))
            self.assertEqual(info['rows_valid'], 251)
            self.assertEqual(info['rows_rejected'], 0)
            self.assertTrue(info['ready_for_research'])
            self.assertEqual(len(out.read_text().splitlines()), 252)

    def test_partial_screens_not_research_ready(self):
        clipboard = '\n'.join([
            'Date\tOpen\tHigh\tLow\tClose\tChange\tVolume',
            'Oct 23, 2026\t61.39\t61.99\t60.00\t61.39\t0.00\t287',
            'Oct 22, 2026\t63\t63\t61.02\t61.39\t-0.6\t2389',
            'Sep 28, 2026\t64.50\t64.50\t64.50\t62.92\t0\t1',
        ])
        with tempfile.TemporaryDirectory() as root:
            out, audit, rejects, info = export('BNWM', clipboard, Path(root))
            self.assertEqual(info['rows_valid'], 2)
            self.assertEqual(info['rows_rejected'], 1)
            self.assertFalse(info['ready_for_research'])
            self.assertIn('impossible_ohlc_relationship', rejects.read_text())


if __name__ == '__main__':
    unittest.main()
