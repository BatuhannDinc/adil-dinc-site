import json
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sync_listings  # noqa: E402


class DisplayTitleTests(unittest.TestCase):
    def test_apostrophe_suffix_is_lowercased_with_dotless_i(self):
        self.assertEqual(sync_listings.to_display_title("KIRKAĞAÇ'TA SATILIK"), "Kırkağaç'ta Satılık")

    def test_leading_digits_kept_and_unit_fixed(self):
        self.assertEqual(sync_listings.to_display_title("20.250M² ARSA"), "20.250m² Arsa")

    def test_fixed_tokens_and_separator(self):
        self.assertEqual(
            sync_listings.to_display_title("OSB'DE DEPO | 10.000 M2 ARSA | 630 KWA GES"),
            "OSB'de Depo – 10.000 m² Arsa – 630 kVA GES",
        )

    def test_turkish_dotted_capitals_lower_correctly(self):
        # Python's str.lower() would turn İ into "i̇" (two code points) and I into "i".
        self.assertEqual(sync_listings.to_display_title("AKHİSAR OSB’DE FABRİKA"), "Akhisar OSB’de Fabrika")

    def test_vocabulary_resolves_ambiguous_capital_i(self):
        vocabulary = sync_listings.build_title_vocabulary(["Akhisar", "Fabrika & Üretim Tesisi", "Kırkağaç"])
        self.assertEqual(
            sync_listings.to_display_title("AKHISAR'DA SATILIK FABRIKA", vocabulary), "Akhisar'da Satılık Fabrika"
        )


class SmallHelperTests(unittest.TestCase):
    def test_parse_price(self):
        self.assertEqual(sync_listings.parse_price("39,500,000"), 39500000)
        self.assertIsNone(sync_listings.parse_price(""))

    def test_type_groups(self):
        self.assertEqual(sync_listings.type_group_of("Fabrika & Üretim Tesisi"), "Sanayi")
        self.assertEqual(sync_listings.type_group_of("Tarla"), "Arsa")
        self.assertEqual(sync_listings.type_group_of("Mağaza"), "Diğer")

    def test_clean_bio_drops_repeated_heading_and_fixes_apostrophes(self):
        bio = 'BAŞLIK\n\nBAŞLIK\n\nKonya"da doğdum. "Alıntı"'
        self.assertEqual(sync_listings.clean_bio(bio), 'BAŞLIK\n\nKonya’da doğdum. "Alıntı"')


class DetailFallbackTests(unittest.TestCase):
    def test_failed_fetch_uses_cache_and_reports_zero_live(self):
        listings = [{"code": "P1", "pageUrl": None}, {"code": "P2", "pageUrl": None}]
        with mock.patch.object(sync_listings, "fetch_listing_detail", side_effect=TimeoutError("slow")), \
                mock.patch.object(sync_listings.time, "sleep"):
            cache, fetched = sync_listings.attach_listing_details(listings, {"P1": {"code": "P1"}})
        self.assertEqual(fetched, 0)
        self.assertEqual(list(cache), ["P1"])
        self.assertEqual([listing["pageUrl"] for listing in listings], ["ilan/P1/", None])

    def test_local_image_paths_for_cover(self):
        url = "https://i.remax.com.tr/photos/20/1511320/L/00a001434f554c89883205b1d7cbdf9a.webp"
        item = {"code": "P9", "title": "X", "cityName": "", "townName": "", "neighborhoodName": "", "area": "",
                "roomOptions": "", "price": "", "oldPrice": "", "currencySymbol": "₺", "operationName": "Satılık",
                "categoryName": "Daire", "images": [url]}
        listing = sync_listings.build_listing(item)
        self.assertEqual(listing["imageMedium"], "media/P9/00a001434f554c89883205b1d7cbdf9a-m.webp")
        self.assertEqual(listing["imageSmall"], "media/P9/00a001434f554c89883205b1d7cbdf9a-s.webp")
        self.assertEqual(listing["image"], url)


class JsonLoggingTests(unittest.TestCase):
    def test_quotes_and_newlines_stay_valid_json(self):
        from json_logging import JsonFormatter
        record = logging.LogRecord("x", logging.WARNING, __file__, 1, 'bad "value"\nnext %s', ("arg",), None)
        entry = json.loads(JsonFormatter("sync_listings").format(record))
        self.assertEqual(entry["message"], 'bad "value"\nnext arg')
        self.assertEqual((entry["severity"], entry["component"]), ("WARNING", "sync_listings"))


if __name__ == "__main__":
    unittest.main()
