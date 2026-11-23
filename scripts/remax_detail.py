"""Turn a RE/MAX listing detail page payload into the cache entry the static pages are built from.

The detail page is a Next.js app like the profile page; the listing record sits in one flight
row under "propertyDetailData", and its long description is hoisted into a separate text row.
Everything here is pure parsing so the network layer stays in sync_listings.py.
"""
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

from description_sanitizer import sanitize_description

# Flight rows are "<hex id>:<json>\n" or "<hex id>:T<hex byte length>,<text>" (no terminator).
FLIGHT_ROW_PATTERN = re.compile(rb"([0-9a-f]+):(?:T([0-9a-f]+),)?")
DETAIL_ANCHOR = '"propertyDetailData"'
TEXT_REFERENCE_PATTERN = re.compile(r"\$([0-9a-f]+)")

# Mainland Turkey plus a small margin; RE/MAX returns 0/0 or swapped pairs for unmapped listings.
TURKEY_MIN_LATITUDE = 35.8
TURKEY_MAX_LATITUDE = 42.2
TURKEY_MIN_LONGITUDE = 25.6
TURKEY_MAX_LONGITUDE = 44.9

# Shown elsewhere on the page (code, category, date) or not useful to a visitor.
HIDDEN_SPEC_LABELS = {"Portföy No", "Emlak Tipi", "Yayınlanma Tarihi"}
# RE/MAX sends some parcel numbers twice under different labels.
SPEC_LABEL_ALIASES = {"Ada No": "Ada", "Parsel No": "Parsel"}

LARGE_IMAGE_PATTERN = re.compile(r"https://i\.remax\.com\.tr/photos/\d+/\d+/L/([0-9a-f]+)\.webp")
LARGE_IMAGE_SEGMENT = "/L/"
MEDIUM_IMAGE_SEGMENT = "/M/"
SMALL_IMAGE_SEGMENT = "/S/"
THUMBNAIL_IMAGE_SEGMENT = "/T/"

# RE/MAX's CDN only answers quickly for variants it already cached, so photos are mirrored locally.
MEDIA_DIR_NAME = "media"
MAX_GALLERY_IMAGES = 24
MEDIUM_SIZE_KEY = "m"
SMALL_SIZE_KEY = "s"

VIMEO_PLAYER_HOST = "player.vimeo.com"
VIMEO_PATH_PATTERN = re.compile(r"/video/(\d+)")
VIMEO_HASH_PATTERN = re.compile(r"[0-9a-f]+")
VIMEO_EMBED_TEMPLATE = "https://player.vimeo.com/video/{video_id}?h={video_hash}"

log = logging.getLogger(__name__)


def flight_rows(payload: str) -> dict:
    """Split a decoded flight payload into rows; T-row lengths count UTF-8 bytes, not characters."""
    buffer = payload.encode("utf-8")
    rows = {}
    position = 0
    while position < len(buffer):
        match = FLIGHT_ROW_PATTERN.match(buffer, position)
        if not match:
            newline = buffer.find(b"\n", position)
            position = len(buffer) if newline < 0 else newline + 1
            continue
        if match.group(2):
            length = int(match.group(2), 16)
            rows[match.group(1).decode()] = buffer[match.end():match.end() + length].decode("utf-8", errors="ignore")
            position = match.end() + length
        else:
            newline = buffer.find(b"\n", match.end())
            newline = len(buffer) if newline < 0 else newline
            rows[match.group(1).decode()] = buffer[match.end():newline].decode("utf-8", errors="ignore")
            position = newline + 1
    return rows


def _walk(node):
    yield node
    children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else []
    for child in children:
        yield from _walk(child)


def extract_property_detail(payload: str) -> dict:
    """Return RE/MAX's raw listing record with the description text reference resolved."""
    rows = flight_rows(payload)
    row = next((value for value in rows.values() if DETAIL_ANCHOR in value), None)
    if row is None:
        raise ValueError("Detail anchor 'propertyDetailData' not found; RE/MAX page structure may have changed")
    holder = next(
        (node for node in _walk(json.loads(row)) if isinstance(node, dict) and "propertyDetailData" in node), None
    )
    if holder is None or not isinstance((holder["propertyDetailData"] or {}).get("data"), dict):
        raise ValueError("propertyDetailData row has no data object")
    detail = dict(holder["propertyDetailData"]["data"])
    detail["description"] = resolve_row_reference(rows, detail.get("description"))
    return detail


def resolve_row_reference(rows: dict, value) -> str:
    match = TEXT_REFERENCE_PATTERN.fullmatch(value or "")
    if not match:
        return value or ""
    if match.group(1) not in rows:
        log.warning("Unresolved description reference %s", value)
        return ""
    return rows[match.group(1)]


def valid_coordinate_pair(latitude, longitude):
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None, None
    inside_turkey = (TURKEY_MIN_LATITUDE <= latitude <= TURKEY_MAX_LATITUDE
                     and TURKEY_MIN_LONGITUDE <= longitude <= TURKEY_MAX_LONGITUDE)
    return (latitude, longitude) if inside_turkey else (None, None)


def visible_specs(header_attributes: dict) -> list:
    specs = []
    seen_labels = set()
    for raw_label, raw_value in (header_attributes or {}).items():
        label = str(raw_label).strip()
        value = str(raw_value if raw_value is not None else "").strip()
        canonical_label = SPEC_LABEL_ALIASES.get(label, label)
        if not label or not value or label in HIDDEN_SPEC_LABELS or canonical_label in seen_labels:
            continue
        seen_labels.add(canonical_label)
        specs.append([label, value])
    return specs


def checked_features(other_attributes: dict) -> list:
    groups = []
    for raw_group, features in (other_attributes or {}).items():
        group = str(raw_group).strip()
        enabled = [str(name).strip() for name, is_on in (features or {}).items() if is_on is True and str(name).strip()]
        if group and enabled:
            groups.append([group, enabled])
    return groups


def image_variants(large_image_url: str):
    """S/M/L variants differ only by one path segment; None if the URL is not a RE/MAX large photo."""
    if not isinstance(large_image_url, str) or not LARGE_IMAGE_PATTERN.fullmatch(large_image_url):
        return None
    return {
        "s": large_image_url.replace(LARGE_IMAGE_SEGMENT, SMALL_IMAGE_SEGMENT),
        "m": large_image_url.replace(LARGE_IMAGE_SEGMENT, MEDIUM_IMAGE_SEGMENT),
        "l": large_image_url,
    }


def remote_variant(large_image_url: str, segment: str) -> str:
    return large_image_url.replace(LARGE_IMAGE_SEGMENT, segment)


def local_image_path(code: str, large_image_url: str, size_key: str):
    """Repo-relative mirror path, e.g. media/P123/<hash>-m.webp; None for non-RE/MAX URLs."""
    match = LARGE_IMAGE_PATTERN.fullmatch(large_image_url or "")
    if not match:
        return None
    return f"{MEDIA_DIR_NAME}/{code}/{match.group(1)}-{size_key}.webp"


def vimeo_embed_url(video_url):
    """Rebuild the player URL from validated parts so no foreign host or extra parameter reaches an iframe."""
    if not isinstance(video_url, str) or not video_url:
        return None
    parts = urlsplit(video_url.strip())
    if parts.scheme != "https" or parts.hostname != VIMEO_PLAYER_HOST or parts.port is not None:
        return None
    path_match = VIMEO_PATH_PATTERN.fullmatch(parts.path)
    video_hash = (parse_qs(parts.query).get("h") or [""])[0]
    if not path_match or not VIMEO_HASH_PATTERN.fullmatch(video_hash):
        return None
    return VIMEO_EMBED_TEMPLATE.format(video_id=path_match.group(1), video_hash=video_hash)


def build_detail_entry(detail: dict, published_on: str):
    """Return (cache entry, phone lines removed) for one raw listing record."""
    latitude, longitude = valid_coordinate_pair(detail.get("latitude"), detail.get("longitude"))
    description_html, phone_lines_removed = sanitize_description(detail.get("description"))
    images = [variants for variants in map(image_variants, detail.get("images") or []) if variants]
    entry = {
        "code": detail["code"],
        "date": published_on,
        "isSold": bool(detail.get("isSold")),
        "latitude": latitude,
        "longitude": longitude,
        "descriptionHtml": description_html,
        "specs": visible_specs(detail.get("headerAttributes")),
        "features": checked_features(detail.get("otherAttributes")),
        "images": images,
        "videoEmbedUrl": vimeo_embed_url(detail.get("videoUrl")),
    }
    return entry, phone_lines_removed
