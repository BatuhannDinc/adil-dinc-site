import copy
import unittest.mock
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_site  # noqa: E402
import remax_detail  # noqa: E402
from test_remax_detail import FIXTURE_CODES, load_fixture_detail  # noqa: E402

BASE_URL = "https://example.test/"
LD_JSON_BLOCK = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
AGENT = {
    "name": "Adil Dinç", "office": "RE/MAX Kuvars", "jobTitle": "Gayrimenkul Danışmanı",
    "phone": "+90 538 891 91 11", "officePhone": "+90 232 362 19 19", "whatsapp": "905388919111",
    "email": "adildinc@remaxkuvars.com", "photo": None, "licenseNumber": "3505441-006",
    "licenseName": "ADİL DİNÇ GAYRİMENKUL YATIRIM DANIŞMANLIĞI",
}


def make_inputs():
    detail_cache, listings = {}, []
    for code in FIXTURE_CODES:
        detail = load_fixture_detail(code)
        entry, _ = remax_detail.build_detail_entry(detail, published_on="2026-07-22")
        detail_cache[code] = entry
        first = entry["images"][0]
        listings.append({
            "code": code, "title": detail["title"].strip(), "displayTitle": detail["title"].strip().title(),
            "city": detail["cityName"], "town": detail["townName"], "neighborhood": detail["neighborhoodName"],
            "area": "100", "rooms": "", "price": 1000, "oldPrice": None, "currency": "₺",
            "operation": detail["operationName"], "category": detail["category"], "image": first["l"],
            "photoCount": len(entry["images"]), "url": f"https://remax.com.tr/tr/portfoy/{code}",
            "imageSmall": remax_detail.local_image_path(code, first["l"], "s"),
            "imageMedium": remax_detail.local_image_path(code, first["l"], "m"), "pageUrl": f"ilan/{code}/", "typeGroup": "Sanayi",
        })
    return {"agent": dict(AGENT), "listings": listings, "reviews": []}, detail_cache


def snapshot(root: Path) -> dict:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


class BuildSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.site_data, self.detail_cache = make_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def test_two_builds_are_byte_identical(self):
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        first = snapshot(self.root)
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        self.assertEqual(first, snapshot(self.root))
        self.assertIn("ilan/P58836361/index.html", first)
        for name in ("sitemap.xml", "robots.txt", "404.html", "ilan/index.html"):
            self.assertIn(name, first)

    def test_injected_markup_in_title_is_escaped(self):
        site_data = copy.deepcopy(self.site_data)
        site_data["listings"][0]["displayTitle"] = '<img src=x onerror=alert(1)>"</script>'
        build_site.build_site(self.root, site_data, self.detail_cache, BASE_URL)
        code = site_data["listings"][0]["code"]
        for page in (self.root / "ilan" / code / "index.html", self.root / "ilan" / "index.html"):
            # JSON-LD carries the raw string as inert data; everything rendered as HTML must be escaped.
            text = LD_JSON_BLOCK.sub("", page.read_text(encoding="utf-8"))
            self.assertFalse("<img src=x" in text, page)
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", text)
        listing_page = (self.root / "ilan" / code / "index.html").read_text(encoding="utf-8")
        ld_blocks = "".join(LD_JSON_BLOCK.findall(listing_page))
        self.assertNotIn("</script>\"", ld_blocks)
        self.assertIn("\\u003c/script>", listing_page)

    def test_removed_listing_directory_is_deleted(self):
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        removed_code = self.site_data["listings"].pop()["code"]
        unrelated = self.root / "ilan" / "not-a-listing"
        unrelated.mkdir()
        result = build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        self.assertEqual(result["removed"], [removed_code])
        self.assertFalse((self.root / "ilan" / removed_code).exists())
        self.assertTrue(unrelated.exists())
        self.assertNotIn(removed_code, (self.root / "sitemap.xml").read_text(encoding="utf-8"))

    def test_pages_have_no_inline_styles_or_executable_inline_scripts(self):
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        for page in self.root.rglob("*.html"):
            text = page.read_text(encoding="utf-8")
            self.assertNotIn("style=", text, page)
            for tag in re.findall(r"<script[^>]*>", text):
                self.assertTrue('type="application/ld+json"' in tag or "src=" in tag, (page, tag))

    def test_sitemap_uses_listing_dates_and_absolute_urls(self):
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        sitemap = (self.root / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("<loc>https://example.test/ilan/P21314620/</loc><lastmod>2026-07-22</lastmod>", sitemap)
        self.assertIn("Sitemap: https://example.test/sitemap.xml", (self.root / "robots.txt").read_text(encoding="utf-8"))

    def test_map_omitted_without_coordinates(self):
        code = FIXTURE_CODES[0]
        self.detail_cache[code] = dict(self.detail_cache[code], latitude=None, longitude=None)
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        page = (self.root / "ilan" / code / "index.html").read_text(encoding="utf-8")
        self.assertFalse("openstreetmap.org/export" in page or "Haritada aç" in page)

    def test_missing_base_url_exits_with_message(self):
        with unittest.mock.patch.dict("os.environ", {"SITE_BASE_URL": "https://example.test"}):
            with self.assertRaises(SystemExit) as raised:
                build_site.read_base_url()
        self.assertIn("SITE_BASE_URL", str(raised.exception))

    def mirror(self, code, image_indexes, size_keys=("s", "m")):
        for index in image_indexes:
            large = self.detail_cache[code]["images"][index]["l"]
            for size_key in size_keys:
                path = self.root / remax_detail.local_image_path(code, large, size_key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"RIFF0000WEBP")

    def test_gallery_uses_only_mirrored_images_with_remote_fallback(self):
        code = FIXTURE_CODES[0]
        images = self.detail_cache[code]["images"]
        self.mirror(code, [0, 1, 2])
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        page = (self.root / "ilan" / code / "index.html").read_text(encoding="utf-8")
        gallery = page[page.index('<div class="gallery">'):page.index("</section>", page.index('<div class="gallery">'))]
        self.assertEqual(gallery.count("<img "), 3)
        self.assertIn(f'src="../../media/{code}/', gallery)
        self.assertIn(f'data-full="../../media/{code}/', gallery)
        self.assertIn(f'data-fallback="{images[1]["l"]}"', gallery)
        self.assertNotIn(images[5]["s"], page)
        self.assertNotIn("/S/", gallery)
        self.assertIn(f"Tüm fotoğraflar RE/MAX ilanında ({len(images)})", gallery)
        self.assertIn(f'<meta property="og:image" content="{BASE_URL}media/{code}/', page)
        hub = (self.root / "ilan" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'<img src="../media/{code}/', hub)

    def test_unmirrored_cover_falls_back_to_remote_large_image(self):
        code = FIXTURE_CODES[1]
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        page = (self.root / "ilan" / code / "index.html").read_text(encoding="utf-8")
        gallery = page[page.index('<div class="gallery">'):]
        self.assertIn(f'<img src="{self.detail_cache[code]["images"][0]["l"]}"', gallery)
        self.assertNotIn('<div class="gallery-thumbs">', gallery[:gallery.index("</section>")])

    def test_build_is_identical_for_same_media(self):
        self.mirror(FIXTURE_CODES[0], range(30))
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        first = snapshot(self.root)
        build_site.build_site(self.root, self.site_data, self.detail_cache, BASE_URL)
        self.assertEqual(first, snapshot(self.root))


if __name__ == "__main__":
    unittest.main()
