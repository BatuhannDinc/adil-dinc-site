import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_images  # noqa: E402
import remax_detail  # noqa: E402

WEBP_BODY = b"RIFF\x10\x00\x00\x00WEBPVP8 data"
LARGE_URL = "https://i.remax.com.tr/photos/13/1447113/L/0fa67c1e9e5c4506885b1b4479816c52.webp"
SECOND_URL = "https://i.remax.com.tr/photos/13/1447113/L/1899f37a00000000000000000000aaaa.webp"


def site_inputs(codes=("P1",), images=(LARGE_URL,)):
    site_data = {"listings": [{"code": code} for code in codes]}
    cache = {code: {"images": [remax_detail.image_variants(url) for url in images]} for code in codes}
    return site_data, cache


class FakeDownloader:
    def __init__(self, answers):
        self.answers = answers  # segment -> body or None
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for segment, body in self.answers.items():
            if segment in url:
                return body
        return None


class MirrorImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_falls_back_in_order_when_preferred_variant_hangs(self):
        downloader = FakeDownloader({"/M/": None, "/L/": WEBP_BODY, "/S/": WEBP_BODY})
        counts = fetch_images.mirror_images(*site_inputs(), self.root, downloader)
        self.assertEqual((counts["downloaded"], counts["fallback"], counts["failed"]), (1, 1, 0))
        medium_calls = [url for url in downloader.calls if "/S/" not in url]
        self.assertEqual([url.split("/")[-2] for url in medium_calls], ["M", "L"])
        self.assertTrue((self.root / "media/P1/0fa67c1e9e5c4506885b1b4479816c52-m.webp").is_file())
        self.assertEqual(list((self.root / "media/P1").glob(".partial-*")), [])

    def test_existing_files_are_reused_without_network(self):
        fetch_images.mirror_images(*site_inputs(), self.root, FakeDownloader({"/": WEBP_BODY}))
        second = FakeDownloader({"/": WEBP_BODY})
        counts = fetch_images.mirror_images(*site_inputs(), self.root, second)
        self.assertEqual(counts["reused"], 2)
        self.assertEqual(second.calls, [])

    def test_failed_image_is_counted_and_not_written(self):
        counts = fetch_images.mirror_images(*site_inputs(), self.root, FakeDownloader({}))
        self.assertEqual(counts["failed"], 2)
        self.assertFalse((self.root / "media/P1").exists())

    def test_only_first_gallery_images_are_mirrored(self):
        urls = [LARGE_URL.replace("0fa67c1e", f"{index:08x}") for index in range(remax_detail.MAX_GALLERY_IMAGES + 5)]
        counts = fetch_images.mirror_images(*site_inputs(images=urls), self.root, FakeDownloader({"/": WEBP_BODY}))
        self.assertEqual(counts["required"], remax_detail.MAX_GALLERY_IMAGES * 2)

    def test_stale_listing_media_is_removed(self):
        for name in ("P999", "not-a-listing"):
            (self.root / "media" / name).mkdir(parents=True)
        counts = fetch_images.mirror_images(*site_inputs(), self.root, FakeDownloader({"/": WEBP_BODY}))
        self.assertEqual(counts["removed"], ["P999"])
        self.assertTrue((self.root / "media/not-a-listing").exists())

    def test_webp_validation(self):
        self.assertTrue(fetch_images.is_webp(WEBP_BODY, "application/octet-stream"))
        self.assertTrue(fetch_images.is_webp(b"x", "image/webp; charset=binary"))
        self.assertFalse(fetch_images.is_webp(b"<html>error</html>", "text/html"))
        self.assertFalse(fetch_images.is_webp(b"", "image/webp"))


if __name__ == "__main__":
    unittest.main()
