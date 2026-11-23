import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import remax_detail  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_CODES = ("P21314620", "P27086060", "P58836361")


def load_fixture_detail(code):
    fixture = json.loads((FIXTURE_DIR / f"{code}.pdd.json").read_text(encoding="utf-8"))
    detail = dict(fixture["pdd"]["data"])
    detail["description"] = remax_detail.resolve_row_reference(fixture["rows"], detail["description"])
    return detail


class FlightRowTests(unittest.TestCase):
    def test_text_row_length_counts_utf8_bytes(self):
        text = "Kırkağaç"  # 8 characters, 11 bytes
        payload = f'0:["$","x"]\n1:T{len(text.encode()):x},{text}2:{{"propertyDetailData":{{"data":{{"code":"P1","description":"$1"}}}}}}\n'
        rows = remax_detail.flight_rows(payload)
        self.assertEqual(rows["1"], text)
        self.assertEqual(remax_detail.extract_property_detail(payload)["description"], text)

    def test_missing_anchor_raises(self):
        with self.assertRaises(ValueError):
            remax_detail.extract_property_detail('0:["$","x"]\n')


class DetailEntryTests(unittest.TestCase):
    def test_fixtures_produce_contract_shaped_entries(self):
        for code in FIXTURE_CODES:
            with self.subTest(code=code):
                entry, _ = remax_detail.build_detail_entry(load_fixture_detail(code), published_on="2026-01-01")
                self.assertEqual(entry["code"], code)
                self.assertIsNotNone(entry["latitude"])
                labels = [label for label, _ in entry["specs"]]
                self.assertFalse({"Portföy No", "Emlak Tipi", "Yayınlanma Tarihi"} & set(labels))
                self.assertEqual(len([label for label in labels if label in ("Ada", "Ada No")]), 1)
                self.assertTrue(all(name == name.strip() and name for _, names in entry["features"] for name in names))
                self.assertTrue(entry["images"])
                for image in entry["images"]:
                    self.assertRegex(image["l"], remax_detail.LARGE_IMAGE_PATTERN)
                    self.assertIn("/S/", image["s"])
                    self.assertIn("/M/", image["m"])
                self.assertNotIn("style=", entry["descriptionHtml"])

    def test_video_url_rebuilt_from_fixture(self):
        entry, _ = remax_detail.build_detail_entry(load_fixture_detail("P21314620"), published_on="2026-08-03")
        self.assertEqual(entry["videoEmbedUrl"], "https://player.vimeo.com/video/1215258993?h=d1c2c59419")


class ValidatorTests(unittest.TestCase):
    def test_non_vimeo_host_rejected(self):
        for url in (
            "https://evil.example/video/123?h=abc",
            "https://player.vimeo.com.evil.example/video/123?h=abc",
            "http://player.vimeo.com/video/123?h=abc",
            "https://player.vimeo.com/video/123?h=abc\"onload=x",
            "javascript:alert(1)",
            "",
            None,
        ):
            with self.subTest(url=url):
                self.assertIsNone(remax_detail.vimeo_embed_url(url))

    def test_vimeo_url_drops_extra_parameters(self):
        self.assertEqual(
            remax_detail.vimeo_embed_url("https://player.vimeo.com/video/42?h=ab12&autoplay=1"),
            "https://player.vimeo.com/video/42?h=ab12",
        )

    def test_coordinates_outside_turkey_become_null(self):
        self.assertEqual(remax_detail.valid_coordinate_pair(0, 0), (None, None))
        self.assertEqual(remax_detail.valid_coordinate_pair(27.6, 39.1), (None, None))
        self.assertEqual(remax_detail.valid_coordinate_pair("39.1", "27.6"), (39.1, 27.6))

    def test_foreign_image_urls_rejected(self):
        self.assertIsNone(remax_detail.image_variants("https://evil.example/photos/1/2/L/ab.webp"))
        self.assertIsNone(remax_detail.image_variants('https://i.remax.com.tr/photos/1/2/L/ab.webp"x'))


if __name__ == "__main__":
    unittest.main()
