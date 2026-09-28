import tempfile
import unittest
from pathlib import Path

from nina_planner.mosaic import (
    load_pointings,
    load_pointings_text,
    parse_header,
    parse_sexagesimal,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

TELESCOPIUS_HEADER = (
    "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), "
    "Pane height (arcmins), Overlap, Row, Column"
)


class ParseSexagesimalTest(unittest.TestCase):
    def test_hours_minutes_seconds(self):
        self.assertAlmostEqual(parse_sexagesimal("0hr 56' 01\""), 0.9336111, places=5)

    def test_degrees_with_symbols(self):
        self.assertAlmostEqual(parse_sexagesimal("45º 51' 18\""), 45.855, places=6)

    def test_space_separated(self):
        self.assertAlmostEqual(parse_sexagesimal("13 56 45"), 13.9458333, places=5)

    def test_colon_separated(self):
        self.assertAlmostEqual(parse_sexagesimal("00:42:44.30"), 0.7123055, places=6)

    def test_signed_colon_separated(self):
        self.assertAlmostEqual(parse_sexagesimal("+41:16:08.5"), 41.2690277, places=5)

    def test_decimal(self):
        self.assertEqual(parse_sexagesimal("45.855"), 45.855)

    def test_numeric_passthrough(self):
        self.assertEqual(parse_sexagesimal(20.85), 20.85)

    def test_leading_negative_sign(self):
        self.assertAlmostEqual(parse_sexagesimal("-45:51:18"), -45.855, places=6)

    def test_south_hemisphere_letter(self):
        self.assertAlmostEqual(parse_sexagesimal("45:51:18 S"), -45.855, places=6)

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_sexagesimal("not-a-coordinate")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_sexagesimal("")


class ParseHeaderTest(unittest.TestCase):
    def test_normalizes_punctuation(self):
        self.assertEqual(parse_header("Position Angle (East)"), "position_angle_east")

    def test_strips_whitespace_and_case(self):
        self.assertEqual(parse_header("  RA  "), "ra")


class LoadPointingsTextTest(unittest.TestCase):
    def test_telescopius_row(self):
        text = (
            TELESCOPIUS_HEADER
            + "\nPane 1, 0hr 56' 01\", 45º 51' 18\", 0.00, 309.00, 205.80, 10%, 1, 1\n"
        )
        pointings = load_pointings_text(text)
        self.assertEqual(len(pointings), 1)
        p = pointings[0]
        self.assertAlmostEqual(p.ra_hours, 0.933611, places=5)
        self.assertAlmostEqual(p.dec_deg, 45.855, places=5)
        self.assertEqual(p.position_angle_deg, 0.0)
        self.assertEqual(p.label, "Pane 1")
        self.assertEqual(p.row, 1)
        self.assertEqual(p.column, 1)

    def test_multiple_rows_preserve_order(self):
        text = (
            TELESCOPIUS_HEADER
            + "\nPane 1, 0hr 56' 01\", 45º 51' 18\", 0.00, 309.00, 205.80, 10%, 1, 1"
            + "\nPane 2, 0hr 29' 28\", 45º 51' 18\", 0.00, 309.00, 205.80, 10%, 1, 2\n"
        )
        pointings = load_pointings_text(text)
        self.assertEqual([p.label for p in pointings], ["Pane 1", "Pane 2"])
        self.assertEqual([p.column for p in pointings], [1, 2])

    def test_bad_coordinate_row_skipped(self):
        text = (
            TELESCOPIUS_HEADER
            + "\nPane 1, garbage, 45º 51' 18\", 0.00, 309.00, 205.80, 10%, 1, 1"
            + "\nPane 2, 0hr 29' 28\", 45º 51' 18\", 0.00, 309.00, 205.80, 10%, 1, 2\n"
        )
        pointings = load_pointings_text(text)
        self.assertEqual([p.label for p in pointings], ["Pane 2"])

    def test_out_of_range_dec_row_skipped(self):
        text = (
            TELESCOPIUS_HEADER
            + "\nPane 1, 0hr 56' 01\", 91º 00' 00\", 0.00, 309.00, 205.80, 10%, 1, 1\n"
        )
        with self.assertRaises(ValueError):
            load_pointings_text(text)

    def test_label_fallback_from_row_and_column(self):
        text = (
            "RA, DEC, Position Angle (East), Row, Column"
            "\n0hr 56' 01\", 45º 51' 18\", 0.00, 2, 3\n"
        )
        pointings = load_pointings_text(text)
        self.assertEqual(pointings[0].label, "r2_c3")

    def test_missing_ra_dec_columns_raises(self):
        text = "Pane, Position Angle (East)\nPane 1, 0.00\n"
        with self.assertRaises(ValueError):
            load_pointings_text(text)

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            load_pointings_text("")

    def test_header_only_raises(self):
        with self.assertRaises(ValueError):
            load_pointings_text(TELESCOPIUS_HEADER + "\n")


class LoadPointingsFileTest(unittest.TestCase):
    def test_reads_repo_mosaic_csv(self):
        path = REPO_ROOT / "mosaic.csv"
        if not path.exists():
            self.skipTest("mosaic.csv not present")
        pointings = load_pointings(path)
        self.assertEqual(len(pointings), 8)
        self.assertEqual(pointings[0].label, "Pane 1")
        self.assertEqual(pointings[0].row, 1)
        self.assertEqual(pointings[0].column, 1)

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                load_pointings(Path(tmp) / "nope.csv")


if __name__ == "__main__":
    unittest.main()
