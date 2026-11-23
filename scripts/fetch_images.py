"""Mirror listing photos (small + medium) into media/<code>/ so pages never hotlink RE/MAX's CDN.

RE/MAX's Cloudflare front answers instantly for variants it has cached but can hang well past
20 seconds on a cache miss, which left empty gallery tiles in browsers. Each size therefore has an
ordered list of acceptable variants; the first one that answers quickly and is a real WebP wins.
"""
import logging
import os
import shutil
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Isolated mode (-I) does not put the script directory on sys.path; the sibling modules are ours.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_site import DATA_PATH, DETAIL_CACHE_PATH, LISTING_CODE_PATTERN, load_detail_cache, load_site_data  # noqa: E402
from json_logging import configure_logging  # noqa: E402
from remax_detail import (  # noqa: E402
    LARGE_IMAGE_SEGMENT, MAX_GALLERY_IMAGES, MEDIA_DIR_NAME, MEDIUM_IMAGE_SEGMENT, MEDIUM_SIZE_KEY, SMALL_IMAGE_SEGMENT,
    SMALL_SIZE_KEY, THUMBNAIL_IMAGE_SEGMENT, LARGE_IMAGE_PATTERN, local_image_path, remote_variant,
)
from sync_listings import USER_AGENT  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent
IMAGE_TIMEOUT_SECONDS = 8
MAX_DOWNLOAD_WORKERS = 4
MIN_IMAGE_SUCCESS_RATIO = 0.5
# Largest RE/MAX L photo seen is well under 1 MB; anything bigger is not a listing photo.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
WEBP_CONTENT_TYPE = "image/webp"
# Medium falls back to the larger L before the blurrier S; small prefers the tiny T over M.
VARIANT_FALLBACK_ORDER = {
    MEDIUM_SIZE_KEY: (MEDIUM_IMAGE_SEGMENT, LARGE_IMAGE_SEGMENT, SMALL_IMAGE_SEGMENT),
    SMALL_SIZE_KEY: (SMALL_IMAGE_SEGMENT, THUMBNAIL_IMAGE_SEGMENT, MEDIUM_IMAGE_SEGMENT),
}

log = logging.getLogger(__name__)


def is_webp(body: bytes, content_type: str) -> bool:
    if not body:
        return False
    has_webp_magic = body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    return has_webp_magic or (content_type or "").split(";")[0].strip().lower() == WEBP_CONTENT_TYPE


def download_image(url: str):
    """Return the image bytes, or None when RE/MAX is slow, errors or sends something that is not WebP."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=IMAGE_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_IMAGE_BYTES + 1)
            if response.status != 200 or len(body) > MAX_IMAGE_BYTES:
                return None
            return body if is_webp(body, response.headers.get("Content-Type")) else None
    except Exception as error:  # timeouts, HTTP errors and resets all mean "try the next variant"
        log.info("Image variant unavailable %s: %s", url, error)
        return None


def write_atomically(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".partial-", suffix=".webp")
    try:
        with os.fdopen(handle, "wb") as temp_file:
            temp_file.write(body)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def plan_downloads(site_data: dict, detail_cache: dict, output_root: Path) -> list:
    """List (target path, candidate URLs) for the first gallery photos of every listing, in data.js order."""
    jobs = []
    for listing in site_data["listings"]:
        code = listing.get("code", "")
        if not LISTING_CODE_PATTERN.fullmatch(code) or code not in detail_cache:
            continue
        for image in detail_cache[code].get("images", [])[:MAX_GALLERY_IMAGES]:
            large_url = image.get("l", "")
            if not LARGE_IMAGE_PATTERN.fullmatch(large_url):
                continue
            for size_key, segments in VARIANT_FALLBACK_ORDER.items():
                target = output_root / local_image_path(code, large_url, size_key)
                jobs.append((target, [remote_variant(large_url, segment) for segment in segments]))
    return jobs


def fetch_one(job, downloader):
    """Return one of "reused", "downloaded", "fallback", "failed"."""
    target, candidates = job
    if target.is_file() and target.stat().st_size > 0:
        return "reused"
    for position, url in enumerate(candidates):
        body = downloader(url)
        if body:
            write_atomically(target, body)
            return "downloaded" if position == 0 else "fallback"
    log.warning("No usable variant for %s", target.name)
    return "failed"


def remove_stale_media(media_root: Path, listing_codes: set) -> list:
    removed = []
    if not media_root.is_dir():
        return removed
    for child in sorted(media_root.iterdir()):
        if child.is_dir() and LISTING_CODE_PATTERN.fullmatch(child.name) and child.name not in listing_codes:
            shutil.rmtree(child)
            removed.append(child.name)
    return removed


def mirror_images(site_data: dict, detail_cache: dict, output_root: Path, downloader=download_image) -> dict:
    jobs = plan_downloads(site_data, detail_cache, output_root)
    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        outcomes = list(pool.map(lambda job: fetch_one(job, downloader), jobs))
    removed = remove_stale_media(output_root / MEDIA_DIR_NAME, {listing.get("code") for listing in site_data["listings"]})
    counts = {outcome: outcomes.count(outcome) for outcome in ("downloaded", "fallback", "reused", "failed")}
    counts["required"] = len(jobs)
    counts["removed"] = removed
    return counts


def main() -> int:
    configure_logging("fetch_images")
    try:
        site_data = load_site_data(DATA_PATH)
        detail_cache = load_detail_cache(DETAIL_CACHE_PATH)
    except (OSError, ValueError) as error:
        log.error("Cannot read image mirror inputs: %s", error)
        return 1
    counts = mirror_images(site_data, detail_cache, REPO_ROOT)
    present = counts["required"] - counts["failed"]
    log.info(
        "Images: %d required, %d downloaded, %d via fallback variant, %d reused, %d failed; removed media dirs: %s",
        counts["required"], counts["downloaded"], counts["fallback"], counts["reused"], counts["failed"],
        ", ".join(counts["removed"]) or "none",
    )
    if counts["required"] and present / counts["required"] < MIN_IMAGE_SUCCESS_RATIO:
        log.error("Only %d of %d images are mirrored; pages will fall back to RE/MAX hotlinks", present, counts["required"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
