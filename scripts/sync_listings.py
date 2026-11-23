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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROFILE_URL = "https://remax.com.tr/tr/danisman/15913-952/adil-dinc"
LISTING_URL_TEMPLATE = "https://remax.com.tr/tr/portfoy/{code}"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data.js"
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "Mozilla/5.0 (compatible; personal-site-sync/1.0)"
MAX_REVIEWS = 8
MIN_REVIEW_LENGTH = 40

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp":"%(asctime)s","severity":"%(levelname)s","component":"sync_listings","message":"%(message)s"}',
)
log = logging.getLogger(__name__)


def fetch_profile_html(url: str) -> str:
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


def build_site_data(record: dict, payload: str) -> dict:
    bio_html = resolve_text_reference(payload, (record.get("description") or {}).get("description", ""))
    listings = [
        {
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
            "image": item["images"][0] if item["images"] else None,
            "photoCount": len(item["images"]),
            "url": LISTING_URL_TEMPLATE.format(code=item["code"]),
        }
        for item in record["properties"]
    ]
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
            "bio": html_to_plain_text(bio_html),
            "officeAddress": f'{office["address"]}, {office["neighborhoodName"]}, {office["townName"]}/{office["cityName"]}',
            "officeLat": office["latitude"],
            "officeLng": office["longitude"],
        },
        "listings": listings,
        "reviews": reviews,
    }


def main() -> int:
    try:
        payload = decode_flight_payload(fetch_profile_html(PROFILE_URL))
        site_data = build_site_data(extract_agent_record(payload), payload)
    except Exception as error:  # surface any structural change on the RE/MAX side with context
        log.error("Sync failed for %s: %s", PROFILE_URL, error)
        return 1
    if not site_data["listings"]:
        # An empty portfolio is far more likely a RE/MAX outage or layout change than reality;
        # fail loudly so the job alerts and the last good data.js stays live.
        log.error("RE/MAX returned 0 listings for %s; keeping previous data.js", PROFILE_URL)
        return 1
    OUTPUT_PATH.write_text(
        "window.SITE_DATA = " + json.dumps(site_data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8"
    )
    log.info("Wrote %d listings and %d reviews to %s", len(site_data["listings"]), len(site_data["reviews"]), OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
