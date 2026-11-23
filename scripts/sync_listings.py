"""Pull the agent's public RE/MAX profile and write listings/profile data for the static site.

The RE/MAX profile page is a Next.js app; the agent record (listings, reviews, contact)
is embedded in the React Server Components payload rather than in visible HTML, so we
decode that payload instead of scraping markup.
"""
import html
import json
import logging
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Isolated mode (-I) does not put the script directory on sys.path; the sibling modules are ours.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from json_logging import configure_logging  # noqa: E402
from remax_detail import (  # noqa: E402
    MEDIUM_SIZE_KEY, SMALL_SIZE_KEY, build_detail_entry, extract_property_detail, local_image_path,
)

PROFILE_URL = "https://remax.com.tr/tr/danisman/15913-952/adil-dinc"
LISTING_URL_TEMPLATE = "https://remax.com.tr/tr/portfoy/{code}"
OUTPUT_PATH = SCRIPTS_DIR.parent / "data.js"
DETAIL_CACHE_PATH = SCRIPTS_DIR / "cache" / "listing_details.json"
LISTING_PAGE_PATH_TEMPLATE = "ilan/{code}/"
REQUEST_TIMEOUT_SECONDS = 30
# Detail pages are fetched one by one; the pause keeps us a polite, low-volume client of RE/MAX.
DETAIL_REQUEST_DELAY_SECONDS = 1.5
USER_AGENT = "Mozilla/5.0 (compatible; personal-site-sync/1.0)"
MAX_REVIEWS = 8
MIN_REVIEW_LENGTH = 40
OTHER_TYPE_GROUP = "Diğer"
# Group RE/MAX's fine-grained categories into the few filters a visitor actually thinks in.
TYPE_GROUPS = {
    "Sanayi": ["Fabrika & Üretim Tesisi"],
    "Çiftlik": ["Çiftlik"],
    "Arsa": ["Konut İmarlı", "Tarla", "Ticari İmarlı", "Sanayi İmarlı"],
    "Konut": ["Daire", "Villa", "Müstakil Ev", "Rezidans"],
}
# Remax titles arrive in ALL CAPS; these tokens keep a fixed spelling after title-casing.
FIXED_TOKENS = {"OSB": "OSB", "KWA": "kVA", "KVA": "kVA", "GES": "GES", "M²": "m²", "M2": "m²"}
APOSTROPHE_PATTERN = re.compile(r"([^'’]+)(['’].*)?")
LEADING_NUMBER_PATTERN = re.compile(r"^(\d[\d.,]*)(.*)$")
# A bare "|" renders like a capital I in the site font, so it becomes an en dash.
TITLE_SEPARATOR_PATTERN = re.compile(r"\s\|\s")
TITLE_SEPARATOR_REPLACEMENT = " – "
LETTER_RUN_PATTERN = re.compile(r"[^\W\d_]+")
MID_WORD_DOTTED_CAPITAL_PATTERN = re.compile(r"[^\W\d_]İ")
FRONT_VOWELS = "eiöü"
BACK_VOWELS = "aıouâ"
# The bio was typed with a straight double quote where an apostrophe belongs (Konya"da).
MISUSED_QUOTE_PATTERN = re.compile(r'(?<=\w)"(?=\w)')
TYPOGRAPHIC_APOSTROPHE = "’"

log = logging.getLogger(__name__)


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def decode_flight_payload(page_html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', page_html, re.S)
    if not chunks:
        raise ValueError("No Next.js flight payload found; RE/MAX page structure may have changed")
    return "".join(json.loads('"' + chunk + '"') for chunk in chunks)


def extract_agent_record(payload: str) -> dict:
    anchor = payload.find('"officeContact"')
    if anchor == -1:
        raise ValueError("Agent record anchor 'officeContact' not found in payload")
    decoder = json.JSONDecoder()
    position = anchor
    while position > 0:
        position = payload.rfind("{", 0, position)
        try:
            candidate, end = decoder.raw_decode(payload, position)
        except json.JSONDecodeError:
            continue
        if end > anchor and "properties" in candidate and "officeContact" in candidate:
            return candidate
    raise ValueError("Could not isolate the agent JSON object around 'officeContact'")


def resolve_text_reference(payload: str, value: str) -> str:
    """Long strings are hoisted into separate RSC rows ("$60" -> row '60:T<hexlen>,<text>')."""
    match = re.fullmatch(r"\$([0-9a-f]+)", value or "")
    if not match:
        return value or ""
    row = re.search(rf"(?:^|[>\n]){match.group(1)}:T([0-9a-f]+),", payload)
    if not row:
        log.warning("Unresolved text reference %s", value)
        return ""
    # The declared length counts UTF-8 bytes, not characters (Turkish letters are multi-byte).
    tail_bytes = payload[row.end():].encode("utf-8")
    return tail_bytes[:int(row.group(1), 16)].decode("utf-8", errors="ignore")


def html_to_plain_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace(" ", " ")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def to_iso_date(remax_date: str) -> str:
    """RE/MAX review dates are day-first (DD-MM-YYYY); browsers would misread them as month-first."""
    return datetime.strptime(remax_date[:10], "%d-%m-%Y").date().isoformat()


def parse_price(raw_price: str):
    digits = re.sub(r"[^\d]", "", raw_price or "")
    return int(digits) if digits else None


def turkish_lower(text: str) -> str:
    # str.lower() maps I to i and İ to i + combining dot; Turkish needs I -> ı and İ -> i.
    return text.replace("I", "ı").replace("İ", "i").lower()


def turkish_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


def _fold_dotted_i(word: str) -> str:
    return turkish_lower(word).replace("ı", "i")


def build_title_vocabulary(texts) -> dict:
    """Map I-ambiguous spellings to the form RE/MAX's own mixed-case text uses (akhisar, fabrika...).

    Many titles were upper-cased with non-Turkish rules, so a capital I may stand for i or ı;
    the listing's own address, category and description reveal which one is meant.
    """
    counts = {}
    for text in texts:
        for word in LETTER_RUN_PATTERN.findall(text or ""):
            if word.isupper() and "I" in word:
                continue  # an all-caps I is exactly the ambiguity we are trying to resolve
            form = turkish_lower(word)
            counts.setdefault(_fold_dotted_i(form), {}).setdefault(form, 0)
            counts[_fold_dotted_i(form)][form] += 1
    return {key: min(forms, key=lambda form: (-forms[form], form)) for key, forms in counts.items()}


def _resolve_by_vowel_harmony(letters: str, preceding: str) -> str:
    """Pick i or ı for each capital I from the nearest vowel, as Turkish suffixes do."""
    resolved = ""
    for index, char in enumerate(letters):
        if char != "I":
            resolved += turkish_lower(char)
            continue
        context = [c for c in reversed(preceding + resolved) if c in FRONT_VOWELS + BACK_VOWELS]
        if not context:
            context = [c for c in turkish_lower(letters[index + 1:]) if c in FRONT_VOWELS + BACK_VOWELS]
        resolved += "i" if context and context[0] in FRONT_VOWELS else "ı"
    return resolved


def _lower_letter_run(run: str, preceding: str, vocabulary: dict, title_is_turkish_cased: bool) -> str:
    if "I" not in run:
        return turkish_lower(run)
    known = vocabulary.get(_fold_dotted_i(run))
    if known:
        return known
    return turkish_lower(run) if title_is_turkish_cased else _resolve_by_vowel_harmony(run, preceding)


def _lower_turkish_text(text: str, preceding: str, vocabulary: dict, title_is_turkish_cased: bool) -> str:
    output = ""
    position = 0
    for match in LETTER_RUN_PATTERN.finditer(text):
        output += text[position:match.start()]
        output += _lower_letter_run(match.group(0), preceding + output, vocabulary, title_is_turkish_cased)
        position = match.end()
    return output + text[position:]


def _title_case_word(word: str, vocabulary: dict, title_is_turkish_cased: bool) -> str:
    apostrophe_match = APOSTROPHE_PATTERN.fullmatch(word)
    stem, suffix = (apostrophe_match.group(1), apostrophe_match.group(2) or "") if apostrophe_match else (word, "")
    number_match = LEADING_NUMBER_PATTERN.match(stem)
    lead, core = (number_match.group(1), number_match.group(2)) if number_match else ("", stem)
    fixed = FIXED_TOKENS.get(turkish_upper(core))
    if fixed:
        lower_core = fixed
    else:
        lower_core = _lower_turkish_text(core, "", vocabulary, title_is_turkish_cased)
        first_letter = next((index for index, char in enumerate(lower_core) if char.isalpha()), None)
        if first_letter is not None:
            lower_core = lower_core[:first_letter] + turkish_upper(lower_core[first_letter]) + lower_core[first_letter + 1:]
    # Suffixes follow the stem's vowel harmony even after loanwords (Akhisar'ın), so never look them up.
    lower_suffix = _resolve_by_vowel_harmony(suffix, lower_core) if not title_is_turkish_cased else turkish_lower(suffix)
    return lead + lower_core + lower_suffix


def to_display_title(title: str, vocabulary: dict = None) -> str:
    """Title-case RE/MAX's ALL CAPS titles with Turkish dotted/dotless I rules."""
    spaced = TITLE_SEPARATOR_PATTERN.sub(TITLE_SEPARATOR_REPLACEMENT, (title or "").strip())
    # A dotted İ inside a word proves the title was upper-cased the Turkish way, so I is always ı.
    title_is_turkish_cased = bool(MID_WORD_DOTTED_CAPITAL_PATTERN.search(spaced))
    return " ".join(_title_case_word(word, vocabulary or {}, title_is_turkish_cased) for word in spaced.split())


def type_group_of(category: str) -> str:
    for group, categories in TYPE_GROUPS.items():
        if category in categories:
            return group
    return OTHER_TYPE_GROUP


def clean_bio(text: str) -> str:
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        paragraph = paragraph.strip()
        # The RE/MAX profile repeats its heading as the first body paragraph.
        if paragraph and (not paragraphs or paragraph != paragraphs[-1]):
            paragraphs.append(paragraph)
    return MISUSED_QUOTE_PATTERN.sub(TYPOGRAPHIC_APOSTROPHE, "\n\n".join(paragraphs))


def build_listing(item: dict) -> dict:
    image = item["images"][0] if item["images"] else None
    return {
        "code": item["code"],
        "title": item["title"].strip(),
        "city": item["cityName"],
        "town": item["townName"],
        "neighborhood": item["neighborhoodName"],
        "area": item["area"],
        "rooms": item["roomOptions"],
        "price": parse_price(item["price"]),
        "oldPrice": parse_price(item["oldPrice"]),
        "currency": item["currencySymbol"],
        "operation": item["operationName"],
        "category": item["categoryName"],
        "image": image,
        "photoCount": len(item["images"]),
        "url": LISTING_URL_TEMPLATE.format(code=item["code"]),
        "displayTitle": to_display_title(item["title"]),
        # Local mirrors written by fetch_images.py; "image" stays the remote fallback.
        "imageSmall": local_image_path(item["code"], image, SMALL_SIZE_KEY),
        "imageMedium": local_image_path(item["code"], image, MEDIUM_SIZE_KEY),
        "pageUrl": None,
        "typeGroup": type_group_of(item["categoryName"]),
    }


def build_site_data(record: dict, payload: str) -> dict:
    bio_html = resolve_text_reference(payload, (record.get("description") or {}).get("description", ""))
    listings = [build_listing(item) for item in record["properties"]]
    reviews = [
        {"name": f'{review["name"]} {(review["surname"] or "")[:1]}.'.strip(), "date": to_iso_date(review["date"]),
         "point": review["point"], "comment": review["comment"].strip()}
        for review in record["reviews"]
        if review.get("comment") and len(review["comment"].strip()) >= MIN_REVIEW_LENGTH
    ][:MAX_REVIEWS]
    office = record["officeContact"]
    return {
        "syncedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourceUrl": PROFILE_URL,
        "agent": {
            "name": record["nameSurname"],
            "jobTitle": record["jobName"],
            "office": record["officeName"],
            "phone": record["phone"],
            "officePhone": record["officePhone"],
            "whatsapp": record["whatsapp"],
            "email": record["email"],
            "instagram": record["instagram"],
            "photo": record["image"],
            "licenseNumber": record["professionalCertificateNumber"],
            "licenseName": record["authorizationLicenseName"],
            "activeYears": record["activeYear"],
            "areas": record["areas"],
            "professions": record["professions"],
            "reviewCount": record["reviewsCount"],
            "reviewAverage": record["reviewsAvgPoint"],
            "bio": clean_bio(html_to_plain_text(bio_html)),
            "officeAddress": f'{office["address"]}, {office["neighborhoodName"]}, {office["townName"]}/{office["cityName"]}',
            "officeLat": office["latitude"],
            "officeLng": office["longitude"],
        },
        "listings": listings,
        "reviews": reviews,
    }


def load_detail_cache() -> dict:
    if not DETAIL_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(DETAIL_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        log.warning("Ignoring unreadable detail cache %s: %s", DETAIL_CACHE_PATH, error)
        return {}


def fetch_listing_detail(code: str):
    """Return (cache entry, phone lines removed) for one listing straight from RE/MAX."""
    detail = extract_property_detail(decode_flight_payload(fetch_html(LISTING_URL_TEMPLATE.format(code=code))))
    if detail.get("code") != code:
        raise ValueError(f"Detail page returned listing {detail.get('code')} instead of {code}")
    return build_detail_entry(detail, published_on=to_iso_date(detail["date"]))


def attach_listing_details(listings: list, previous_cache: dict) -> dict:
    """Fetch every detail page; a failed fetch keeps the last good cached copy so its page survives.

    Returns (detail cache, number of details fetched live).
    """
    detail_cache = {}
    fetched_count = cached_count = phone_lines_removed = 0
    for index, listing in enumerate(listings):
        if index:
            time.sleep(DETAIL_REQUEST_DELAY_SECONDS)
        code = listing["code"]
        try:
            entry, removed = fetch_listing_detail(code)
            detail_cache[code] = entry
            phone_lines_removed += removed
            fetched_count += 1
        except Exception as error:  # one broken listing must not take down the whole sync
            if code in previous_cache:
                detail_cache[code] = previous_cache[code]
                cached_count += 1
                log.warning("Detail fetch failed for %s, using cached copy: %s", code, error)
            else:
                log.warning("Detail fetch failed for %s and no cached copy exists; listing gets no page: %s", code, error)
        if code in detail_cache:
            listing["pageUrl"] = LISTING_PAGE_PATH_TEMPLATE.format(code=code)
    log.info(
        "Listing details: %d fetched, %d from cache, %d without page, %d phone lines removed from descriptions",
        fetched_count, cached_count, len(listings) - fetched_count - cached_count, phone_lines_removed,
    )
    return detail_cache, fetched_count


def title_vocabulary_texts(site_data: dict, detail_cache: dict):
    agent = site_data["agent"]
    yield from agent["areas"]
    yield from agent["professions"]
    for listing in site_data["listings"]:
        yield from (listing["city"], listing["town"], listing["neighborhood"], listing["category"])
    for entry in detail_cache.values():
        yield html_to_plain_text(entry["descriptionHtml"])
        for label, value in entry["specs"]:
            yield label
            yield value
        for group, features in entry["features"]:
            yield group
            yield from features


def apply_display_titles(site_data: dict, detail_cache: dict) -> None:
    vocabulary = build_title_vocabulary(title_vocabulary_texts(site_data, detail_cache))
    for listing in site_data["listings"]:
        listing["displayTitle"] = to_display_title(listing["title"], vocabulary)


def main() -> int:
    configure_logging("sync_listings")
    try:
        payload = decode_flight_payload(fetch_html(PROFILE_URL))
        site_data = build_site_data(extract_agent_record(payload), payload)
    except Exception as error:  # surface any structural change on the RE/MAX side with context
        log.error("Sync failed for %s: %s", PROFILE_URL, error)
        return 1
    if not site_data["listings"]:
        # An empty portfolio is far more likely a RE/MAX outage or layout change than reality;
        # fail loudly so the job alerts and the last good data.js stays live.
        log.error("RE/MAX returned 0 listings for %s; keeping previous data.js", PROFILE_URL)
        return 1
    detail_cache, fetched_count = attach_listing_details(site_data["listings"], load_detail_cache())
    apply_display_titles(site_data, detail_cache)
    DETAIL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DETAIL_CACHE_PATH.write_text(json.dumps(detail_cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    OUTPUT_PATH.write_text(
        "window.SITE_DATA = " + json.dumps(site_data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8"
    )
    log.info("Wrote %d listings and %d reviews to %s", len(site_data["listings"]), len(site_data["reviews"]), OUTPUT_PATH)
    if not fetched_count:
        # Files are still written so the site deploys from cache, but a run with no live detail
        # page means RE/MAX changed or blocked us; the non-zero exit makes the workflow alert.
        log.error("No listing detail page could be fetched live; pages are served from cache only")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
