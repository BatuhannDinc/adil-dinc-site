"""Render crawlable static pages (one per listing, a portfolio hub, sitemap, robots, 404) offline.

Inputs are data.js and the detail cache written by sync_listings.py; nothing here touches the
network. Output must be byte-identical for identical inputs so the deploy job only commits real
changes, which is why no timestamps or unordered iteration ever reach the HTML.
"""
import html
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

# Isolated mode (-I) does not put the script directory on sys.path; the sibling modules are ours.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from description_sanitizer import sanitize_description  # noqa: E402
from json_logging import configure_logging  # noqa: E402
from remax_detail import (  # noqa: E402
    MAX_GALLERY_IMAGES, MEDIA_DIR_NAME, MEDIUM_SIZE_KEY, SMALL_SIZE_KEY, image_variants, local_image_path,
    valid_coordinate_pair, vimeo_embed_url,
)

REPO_ROOT = SCRIPTS_DIR.parent
DATA_PATH = REPO_ROOT / "data.js"
DETAIL_CACHE_PATH = SCRIPTS_DIR / "cache" / "listing_details.json"
INDEX_PATH = REPO_ROOT / "index.html"
LISTING_DIR_NAME = "ilan"
KVKK_DIR_NAME = "kvkk"
DATA_JS_PREFIX = "window.SITE_DATA = "
DATA_JS_SUFFIX = ";"
SITE_BASE_URL_ENV = "SITE_BASE_URL"

LISTING_CODE_PATTERN = re.compile(r"P\d+")
BASE_URL_PATTERN = re.compile(r"https?://\S+/")
LISTING_PAGE_ROOT = "../../"
HUB_PAGE_ROOT = "../"
RENT_OPERATION = "Kiralık"
GALLERY_THUMB_COUNT = 12
SIMILAR_LISTING_COUNT = 3
# Roughly 1 km around the pin: enough context to recognise the area, tight enough to show the parcel.
OSM_BBOX_DELTA = 0.01
OSM_MARKER_ZOOM = 16
COORDINATE_DECIMALS = 6
# Measured with sips on RE/MAX's S and M variants (L 1999x1324 -> M 1200x794, S 600x397); aspect
# varies per photo, the attributes only reserve layout space before the image loads.
SMALL_IMAGE_WIDTH = 600
SMALL_IMAGE_HEIGHT = 397
MEDIUM_IMAGE_WIDTH = 1200
MEDIUM_IMAGE_HEIGHT = 794
GALLERY_MAIN_SIZES = "(min-width: 960px) 66vw, 100vw"
CARD_IMAGE_SIZES = "(min-width: 960px) 33vw, 100vw"
TYPE_GROUP_ORDER = ["Sanayi", "Çiftlik", "Arsa", "Konut", "Diğer"]
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' https://i.remax.com.tr; "
    "frame-src https://www.openstreetmap.org https://player.vimeo.com; "
    "script-src 'self'; style-src 'self'; font-src 'self'"
)
SPRITE_SYMBOLS = (
    '<symbol id="i-wa" viewBox="0 0 24 24"><path fill="currentColor" d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2.05 22l5.25-1.38a9.9 9.9 0 0 0 4.74 1.21h.01c5.46 0 9.91-4.45 9.91-9.91A9.86 9.86 0 0 0 12.04 2Zm5.8 14.09c-.24.68-1.42 1.3-1.95 1.35-.5.05-.97.23-3.27-.68-2.77-1.09-4.52-3.92-4.66-4.1-.13-.18-1.11-1.48-1.11-2.82s.7-2 .96-2.27c.25-.27.54-.34.72-.34l.52.01c.17 0 .39-.06.61.47.24.56.79 1.94.86 2.08.07.13.11.3.02.48-.09.18-.13.29-.27.45-.13.16-.28.35-.4.47-.13.13-.27.28-.12.55.16.27.7 1.15 1.5 1.86 1.03.92 1.9 1.2 2.17 1.34.27.13.43.11.58-.07.16-.18.67-.78.85-1.05.18-.27.36-.22.61-.13.25.09 1.6.75 1.87.89.27.13.45.2.52.31.07.11.07.65-.17 1.3Z"/></symbol>\n  '
    '<symbol id="i-phone" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92Z"/></symbol>\n  '
    '<symbol id="i-arrow" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M7 17 17 7M8 7h9v9"/></symbol>'
)

log = logging.getLogger(__name__)


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def json_ld(payload) -> str:
    # Escaping every "<" rules out both "</script>" and "<!--" breaking out of the data block.
    return json.dumps(payload, ensure_ascii=False, indent=1).replace("<", "\\u003c")


def load_site_data(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    if not (text.startswith(DATA_JS_PREFIX) and text.endswith(DATA_JS_SUFFIX)):
        raise ValueError(f"{path} does not look like sync_listings.py output")
    return json.loads(text[len(DATA_JS_PREFIX):-len(DATA_JS_SUFFIX)])


def load_detail_cache(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_base_url() -> str:
    base_url = os.environ.get(SITE_BASE_URL_ENV, "").strip()
    if not BASE_URL_PATTERN.fullmatch(base_url):
        raise SystemExit(
            f"{SITE_BASE_URL_ENV} must be set to the absolute site URL ending with '/', "
            f"e.g. https://example.com/ (got {base_url!r})"
        )
    return base_url


# --- formatting ------------------------------------------------------------------------------
def format_amount(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


def price_html(listing: dict) -> str:
    if not listing.get("price"):
        return "Fiyat için arayın"
    currency = esc(listing.get("currency"))
    old = f"<s>{format_amount(listing['oldPrice'])} {currency}</s> " if listing.get("oldPrice") else ""
    suffix = " <small>/ ay</small>" if listing.get("operation") == RENT_OPERATION else ""
    return f"{old}{format_amount(listing['price'])} {currency}{suffix}"


def price_text(listing: dict) -> str:
    if not listing.get("price"):
        return "Fiyat için arayın"
    suffix = " / ay" if listing.get("operation") == RENT_OPERATION else ""
    return f"{format_amount(listing['price'])} {listing.get('currency') or ''}{suffix}".strip()


def tel_href(phone: str) -> str:
    return "tel:" + re.sub(r"[^\d+]", "", phone or "")


def whatsapp_href(agent: dict, message: str) -> str:
    return f"https://wa.me/{re.sub(r'[^0-9]', '', agent.get('whatsapp') or '')}?text={quote(message, safe='')}"


def listing_message(listing: dict, link: str) -> str:
    return f"Merhaba Adil Bey, {listing['code']} numaralı ilanınız hakkında bilgi almak istiyorum: {link}"


def format_iso_date(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{day}.{month}.{year}"


# --- shared chrome ---------------------------------------------------------------------------
def page_head(root: str, title: str, description: str, canonical: str = None, og_image: str = None,
              json_ld_payload=None, noindex: bool = False) -> str:
    lines = [
        "<!doctype html>",
        '<html lang="tr">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{esc(CONTENT_SECURITY_POLICY)}">',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
    ]
    if noindex:
        lines.append('<meta name="robots" content="noindex">')
    if canonical:
        lines += [
            f'<link rel="canonical" href="{esc(canonical)}">',
            f'<meta property="og:url" content="{esc(canonical)}">',
        ]
    lines += [
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
    ]
    if og_image:
        lines.append(f'<meta property="og:image" content="{esc(og_image)}">')
    lines += [
        f'<link rel="icon" href="{esc(root)}assets/favicon.svg" type="image/svg+xml">',
        f'<link rel="stylesheet" href="{esc(root)}assets/site.css">',
        f'<script src="{esc(root)}assets/site.js" defer></script>',
    ]
    if json_ld_payload is not None:
        lines.append(f'<script type="application/ld+json">\n{json_ld(json_ld_payload)}\n</script>')
    lines.append("</head>")
    return "\n".join(lines)


def page_header(root: str, agent: dict, current: str = None) -> str:
    nav_items = [
        ("Portföy", f"{root}{LISTING_DIR_NAME}/", "portfolio"),
        ("Uzmanlık", f"{root}#uzmanlik", None),
        ("Hakkımda", f"{root}#hakkimda", None),
        ("Değerleme", f"{root}#degerleme", None),
        ("Kredi Hesaplama", f"{root}#kredi", None),
        ("İletişim", f"{root}#iletisim", None),
    ]
    current_attr = ' aria-current="page"'
    links = "\n".join(
        f'      <a href="{esc(href)}"{current_attr if key and key == current else ""}>{esc(label)}</a>'
        for label, href, key in nav_items
    )
    return f"""<a class="skip-link" href="#main">İçeriğe geç</a>

<svg class="sprite" aria-hidden="true" focusable="false">
  {SPRITE_SYMBOLS}
</svg>

<header class="nav">
  <div class="wrap">
    <a class="brand" href="{esc(root)}"><strong>{esc(agent['name'])}</strong><span>{esc(agent['office'])}</span></a>
    <nav id="site-menu" class="nav-links" aria-label="Ana menü">
{links}
    </nav>
    <a class="btn btn-dark btn-sm nav-call" href="{esc(tel_href(agent['phone']))}"><span class="label">Hemen Ara</span> <span class="icon" aria-hidden="true"><svg><use href="#i-phone"/></svg></span></a>
    <button class="nav-toggle" type="button" aria-controls="site-menu" aria-expanded="false" aria-label="Menüyü aç"><span class="nav-toggle-lines" aria-hidden="true"><span></span></span></button>
  </div>
</header>"""


def page_footer(root: str, agent: dict) -> str:
    return f"""<footer class="site">
  <div class="wrap">
    <p>© {esc(agent['name'])}, {esc(agent['office'])}. Yetki Belgesi No: {esc(agent['licenseNumber'])} – {esc(agent['licenseName'])}</p>
    <ul class="footer-links">
      <li><a href="{esc(root)}{KVKK_DIR_NAME}/">KVKK Aydınlatma Metni</a></li>
      <li><a href="{esc(root)}{LISTING_DIR_NAME}/">Portföy</a></li>
    </ul>
    <p>Her RE/MAX ofisi bağımsız olarak sahiplenilmiş ve işletilmektedir.</p>
  </div>
</footer>
</body>
</html>
"""


def agent_json_ld(agent: dict, base_url: str) -> dict:
    return {
        "@type": "RealEstateAgent",
        "name": agent["name"],
        "jobTitle": agent.get("jobTitle"),
        "image": agent.get("photo"),
        "telephone": agent.get("phone"),
        "email": agent.get("email"),
        "url": base_url,
        "worksFor": {"@type": "RealEstateAgent", "name": agent.get("office"), "telephone": agent.get("officePhone")},
    }


def breadcrumb_json_ld(items) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": position, "name": name, "item": url}
            for position, (name, url) in enumerate(items, start=1)
        ],
    }


# --- mirrored media --------------------------------------------------------------------------
def existing_media(output_root: Path) -> frozenset:
    """Relative paths of mirrored photos; the build only references files fetch_images.py produced."""
    media_root = output_root / MEDIA_DIR_NAME
    if not media_root.is_dir():
        return frozenset()
    return frozenset(path.relative_to(output_root).as_posix() for path in media_root.rglob("*.webp") if path.is_file())


def mirrored_path(media: frozenset, code: str, large_url: str, size_key: str):
    path = local_image_path(code, large_url, size_key)
    return path if path in media else None


# --- cards -----------------------------------------------------------------------------------
def card_link(listing: dict, root: str, base_url: str, has_page: bool):
    if has_page:
        return f"{root}{LISTING_DIR_NAME}/{listing['code']}/", f"{base_url}{LISTING_DIR_NAME}/{listing['code']}/", False
    return listing["url"], listing["url"], True


def render_card(listing: dict, agent: dict, root: str, base_url: str, has_page: bool, media: frozenset) -> str:
    href, absolute_href, external = card_link(listing, root, base_url, has_page)
    external_attrs = ' target="_blank" rel="noopener"' if external else ""
    remote_image = listing.get("image")
    medium = listing.get("imageMedium") if listing.get("imageMedium") in media else None
    small = listing.get("imageSmall") if listing.get("imageSmall") in media else None
    image = ""
    if medium:
        srcset = f' srcset="{esc(root + small)} {SMALL_IMAGE_WIDTH}w, {esc(root + medium)} {MEDIUM_IMAGE_WIDTH}w" sizes="{CARD_IMAGE_SIZES}"' if small else ""
        fallback = f' data-fallback="{esc(remote_image)}"' if remote_image else ""
        image = (f'<img src="{esc(root + medium)}"{srcset} width="{SMALL_IMAGE_WIDTH}" height="{SMALL_IMAGE_HEIGHT}" '
                 f'loading="lazy"{fallback} alt="">')
    elif remote_image:
        image = f'<img src="{esc(remote_image)}" width="{SMALL_IMAGE_WIDTH}" height="{SMALL_IMAGE_HEIGHT}" loading="lazy" alt="">'
    rent_class = " rent" if listing.get("operation") == RENT_OPERATION else ""
    meta = [f"<span>{esc(listing['area'])} m²</span>"] if listing.get("area") else []
    if listing.get("rooms"):
        meta.append(f"<span>{esc(listing['rooms'])}</span>")
    meta.append(f"<span>{esc(listing.get('photoCount', 0))} fotoğraf</span>")
    return f"""<li>
<article class="card">
  <a class="card-media" href="{esc(href)}"{external_attrs} tabindex="-1" aria-hidden="true">{image}</a>
  <div class="card-body">
    <span class="card-op{rent_class}">{esc(listing['operation'])} {esc(listing['category'])}</span>
    <h3><a href="{esc(href)}"{external_attrs}>{esc(listing['displayTitle'])}</a></h3>
    <div class="card-loc">{esc(listing['town'])}, {esc(listing['city'])}</div>
    <div class="card-meta">{"".join(meta)}</div>
    <div class="card-foot"><span class="price">{price_html(listing)}</span><a class="wa-mini" href="{esc(whatsapp_href(agent, listing_message(listing, absolute_href)))}" target="_blank" rel="noopener" aria-label="{esc(listing['code'])} numaralı ilan için WhatsApp'tan yaz"><svg aria-hidden="true"><use href="#i-wa"/></svg></a></div>
  </div>
</article>
</li>"""


# --- listing page ----------------------------------------------------------------------------
def gallery_link(full_src: str, thumb_src: str, fallback: str, alt: str) -> str:
    return (
        f'<a href="{esc(full_src)}" data-full="{esc(full_src)}"><img src="{esc(thumb_src)}" '
        f'width="{SMALL_IMAGE_WIDTH}" height="{SMALL_IMAGE_HEIGHT}" loading="lazy" data-fallback="{esc(fallback)}" alt="{esc(alt)}"></a>'
    )


def render_main_image(listing: dict, first: dict, media: frozenset, root: str) -> str:
    alt = f"{listing['displayTitle']} – fotoğraf 1"
    medium = mirrored_path(media, listing["code"], first["l"], MEDIUM_SIZE_KEY)
    if not medium:
        # The cover is the one photo worth a hotlink: without it the page has no image at all.
        return (f'<a class="gallery-main" href="{esc(first["l"])}" data-full="{esc(first["l"])}">'
                f'<img src="{esc(first["l"])}" width="{MEDIUM_IMAGE_WIDTH}" height="{MEDIUM_IMAGE_HEIGHT}" '
                f'fetchpriority="high" alt="{esc(alt)}"></a>')
    small = mirrored_path(media, listing["code"], first["l"], SMALL_SIZE_KEY)
    srcset = f' srcset="{esc(root + small)} {SMALL_IMAGE_WIDTH}w, {esc(root + medium)} {MEDIUM_IMAGE_WIDTH}w" sizes="{GALLERY_MAIN_SIZES}"' if small else ""
    return (f'<a class="gallery-main" href="{esc(root + medium)}" data-full="{esc(root + medium)}">'
            f'<img src="{esc(root + medium)}"{srcset} width="{MEDIUM_IMAGE_WIDTH}" height="{MEDIUM_IMAGE_HEIGHT}" '
            f'fetchpriority="high" data-fallback="{esc(first["l"])}" alt="{esc(alt)}"></a>')


def render_gallery(listing: dict, images: list, media: frozenset, root: str) -> str:
    if not images:
        return ""
    title = listing["displayTitle"]
    thumbs = []
    for number, variants in enumerate(images[1:MAX_GALLERY_IMAGES], start=2):
        medium = mirrored_path(media, listing["code"], variants["l"], MEDIUM_SIZE_KEY)
        small = mirrored_path(media, listing["code"], variants["l"], SMALL_SIZE_KEY)
        if not (medium or small):
            continue  # unmirrored variants can hang for 25 s at RE/MAX's origin; leave them out
        thumbs.append(gallery_link(root + (medium or small), root + (small or medium), variants["l"],
                                   f"{title} – fotoğraf {number}"))
    visible = thumbs[:GALLERY_THUMB_COUNT]
    hidden = thumbs[GALLERY_THUMB_COUNT:]
    shown_count = 1 + len(thumbs)
    parts = [render_main_image(listing, images[0], media, root)]
    if visible:
        parts.append('<div class="gallery-thumbs">\n' + "\n".join(visible) + "\n</div>")
    if hidden:
        parts.append(
            f"<details>\n<summary>Tüm fotoğraflar ({shown_count})</summary>\n"
            '<div class="gallery-thumbs">\n' + "\n".join(hidden) + "\n</div>\n</details>"
        )
    if len(images) > shown_count:
        parts.append(f'<p><a href="{esc(listing["url"])}" target="_blank" rel="noopener">'
                     f"Tüm fotoğraflar RE/MAX ilanında ({len(images)})</a></p>")
    body = "\n".join(parts)
    return f"""<section aria-labelledby="gallery-title">
<h2 id="gallery-title" class="visually-hidden">Fotoğraflar</h2>
<div class="gallery">
{body}
</div>
</section>"""


def render_specs(listing: dict, specs: list) -> str:
    rows = [("Portföy No", listing["code"])] + [(label, value) for label, value in specs]
    body = "\n".join(f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>" for label, value in rows)
    return f"""<section aria-labelledby="specs-title">
<h2 id="specs-title">İlan bilgileri</h2>
<dl class="spec-table">
{body}
</dl>
</section>"""


def render_features(features: list) -> str:
    if not features:
        return ""
    groups = "\n".join(
        f"<div>\n<h3>{esc(group)}</h3>\n<ul>{''.join(f'<li>{esc(name)}</li>' for name in names)}</ul>\n</div>"
        for group, names in features
    )
    return f"""<section aria-labelledby="features-title">
<h2 id="features-title">Özellikler</h2>
<div class="feature-groups">
{groups}
</div>
</section>"""


def render_description(description_html: str) -> str:
    # Re-sanitised at build time so a hand-edited cache can never inject markup into the page.
    clean_html, _ = sanitize_description(description_html)
    if not clean_html:
        return ""
    return f"""<section aria-labelledby="description-title">
<h2 id="description-title">Açıklama</h2>
<div class="listing-description">
{clean_html}
</div>
</section>"""


def render_map(listing: dict, detail: dict) -> str:
    latitude, longitude = valid_coordinate_pair(detail.get("latitude"), detail.get("longitude"))
    if latitude is None:
        return ""

    def coordinate(value: float) -> str:
        return f"{value:.{COORDINATE_DECIMALS}f}"

    bbox = ",".join(coordinate(value) for value in (
        longitude - OSM_BBOX_DELTA, latitude - OSM_BBOX_DELTA, longitude + OSM_BBOX_DELTA, latitude + OSM_BBOX_DELTA,
    ))
    lat, lng = coordinate(latitude), coordinate(longitude)
    embed_url = f"https://www.openstreetmap.org/export/embed.html?bbox={bbox}&layer=mapnik&marker={lat},{lng}"
    open_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map={OSM_MARKER_ZOOM}/{lat}/{lng}"
    return f"""<section aria-labelledby="map-title">
<h2 id="map-title">Konum</h2>
<div class="listing-map"><iframe title="{esc(listing['displayTitle'])} konum haritası" src="{esc(embed_url)}" loading="lazy"></iframe></div>
<p><a href="{esc(open_url)}" target="_blank" rel="noopener">Haritada aç</a></p>
</section>"""


def render_video(listing: dict, detail: dict) -> str:
    embed_url = vimeo_embed_url(detail.get("videoEmbedUrl"))
    if not embed_url:
        return ""
    return f"""<section aria-labelledby="video-title">
<h2 id="video-title">Video</h2>
<div class="listing-video"><iframe title="{esc(listing['displayTitle'])} videosu" src="{esc(embed_url)}" loading="lazy" allow="fullscreen; picture-in-picture" allowfullscreen></iframe></div>
</section>"""


def render_aside(listing: dict, agent: dict, page_url: str) -> str:
    return f"""<aside class="listing-aside">
<p class="listing-price">{price_html(listing)}</p>
<div class="listing-cta">
<p>Yer gösterimi ve detaylı bilgi için yazın ya da arayın.</p>
<a class="btn btn-wa" href="{esc(whatsapp_href(agent, listing_message(listing, page_url)))}" target="_blank" rel="noopener">WhatsApp'tan yazın <span class="icon" aria-hidden="true"><svg><use href="#i-wa"/></svg></span></a>
<a class="btn btn-dark" href="{esc(tel_href(agent['phone']))}">{esc(agent['phone'])} <span class="icon" aria-hidden="true"><svg><use href="#i-phone"/></svg></span></a>
</div>
<p class="license-note">Yetki Belgesi No: {esc(agent['licenseNumber'])} – {esc(agent['licenseName'])}</p>
<p class="source-note">Kaynak: <a href="{esc(listing['url'])}" target="_blank" rel="noopener">RE/MAX ilanı {esc(listing['code'])}</a></p>
</aside>"""


def similar_listings(listing: dict, listings: list) -> list:
    return [
        other for other in listings
        if other["code"] != listing["code"] and other.get("typeGroup") == listing.get("typeGroup")
    ][:SIMILAR_LISTING_COUNT]


def render_listing_page(listing: dict, detail: dict, site_data: dict, base_url: str, page_codes: set,
                        media: frozenset) -> str:
    agent = site_data["agent"]
    code = listing["code"]
    root = LISTING_PAGE_ROOT
    page_url = f"{base_url}{LISTING_DIR_NAME}/{code}/"
    hub_url = f"{base_url}{LISTING_DIR_NAME}/"
    title = listing["displayTitle"]
    images = [variants for variants in (image_variants(image.get("l")) for image in detail.get("images", [])) if variants]
    location = ", ".join(part for part in (listing.get("neighborhood"), listing.get("town"), listing.get("city")) if part)
    description = f"{title}. {listing.get('town')}, {listing.get('city')}. {listing['operation']}: {price_text(listing)}"
    cover_medium = mirrored_path(media, code, images[0]["l"], MEDIUM_SIZE_KEY) if images else None
    og_image = base_url + cover_medium if cover_medium else (images[0]["l"] if images else listing.get("image"))
    structured_data = {
        "@context": "https://schema.org",
        "@graph": [
            breadcrumb_json_ld([("Ana Sayfa", base_url), ("Portföy", hub_url), (title, page_url)]),
            agent_json_ld(agent, base_url),
        ],
    }
    rent_class = " rent" if listing.get("operation") == RENT_OPERATION else ""
    discount = ' <span class="badge-discount">İndirimli</span>' if listing.get("oldPrice") else ""
    published = f" · Yayın tarihi: {esc(format_iso_date(detail['date']))}" if detail.get("date") else ""
    similar = similar_listings(listing, site_data["listings"])
    similar_section = ""
    if similar:
        cards = "\n".join(render_card(other, agent, root, base_url, other["code"] in page_codes, media) for other in similar)
        similar_section = f"""<section aria-labelledby="similar-title" class="section-flush">
<div class="section-head">
<h2 id="similar-title">Benzer ilanlar</h2>
</div>
<ul class="portfolio-list">
{cards}
</ul>
</section>"""
    main_sections = "\n".join(part for part in (
        render_gallery(listing, images, media, root),
        render_specs(listing, detail.get("specs", [])),
        render_features(detail.get("features", [])),
        render_description(detail.get("descriptionHtml", "")),
        render_map(listing, detail),
        render_video(listing, detail),
    ) if part)
    return f"""{page_head(root, f"{title} | {agent['name']}", description, page_url, og_image, structured_data)}
<body id="top">
{page_header(root, agent, current="portfolio")}

<main id="main" tabindex="-1">
<div class="wrap">
<nav class="breadcrumb" aria-label="Sayfa konumu">
<ol>
<li><a href="{root}">Ana Sayfa</a></li>
<li><a href="../">Portföy</a></li>
<li><span aria-current="page">{esc(title)}</span></li>
</ol>
</nav>

<header class="listing-hero">
<span class="card-op{rent_class}">{esc(listing['operation'])} {esc(listing['category'])}</span>{discount}
<h1>{esc(title)}</h1>
<p>{esc(location)}</p>
<p class="lead">Portföy No: {esc(code)}{published}</p>
</header>

<div class="listing-layout">
<div class="listing-main">
{main_sections}
</div>
{render_aside(listing, agent, page_url)}
</div>

{similar_section}
</div>
</main>

{page_footer(root, agent)}"""


# --- hub, sitemap, robots, 404 ---------------------------------------------------------------
def render_hub_page(site_data: dict, base_url: str, page_codes: set, media: frozenset) -> str:
    agent = site_data["agent"]
    root = HUB_PAGE_ROOT
    hub_url = f"{base_url}{LISTING_DIR_NAME}/"
    sections = []
    for group in TYPE_GROUP_ORDER:
        members = [listing for listing in site_data["listings"] if listing.get("typeGroup", "Diğer") == group]
        if not members:
            continue
        cards = "\n".join(render_card(listing, agent, root, base_url, listing["code"] in page_codes, media) for listing in members)
        slug = re.sub(r"[^a-z]", "", group.lower().translate(str.maketrans("çğıöşü", "cgiosu")))
        sections.append(f"""<section aria-labelledby="group-{slug}">
<h2 id="group-{slug}">{esc(group)} ({len(members)})</h2>
<ul class="portfolio-list">
{cards}
</ul>
</section>""")
    structured_data = {
        "@context": "https://schema.org",
        "@graph": [breadcrumb_json_ld([("Ana Sayfa", base_url), ("Portföy", hub_url)]), agent_json_ld(agent, base_url)],
    }
    description = (f"{agent['name']} ({agent['office']}) satılık ve kiralık portföyü: "
                   f"{len(site_data['listings'])} ilan; fabrika, çiftlik, arsa ve konut.")
    body = "\n\n".join(sections)
    return f"""{page_head(root, f"Portföy | {agent['name']}", description, hub_url, agent.get('photo'), structured_data)}
<body id="top">
{page_header(root, agent, current="portfolio")}

<main id="main" tabindex="-1">
<div class="wrap">
<nav class="breadcrumb" aria-label="Sayfa konumu">
<ol>
<li><a href="{root}">Ana Sayfa</a></li>
<li><span aria-current="page">Portföy</span></li>
</ol>
</nav>
<div class="section-head">
<h1>Portföy</h1>
<p class="lead">Satılık ve kiralık tüm ilanlarım. Fotoğraflar, özellikler ve konum için ilana tıklayın.</p>
</div>

{body}
</div>
</main>

{page_footer(root, agent)}"""


def render_not_found_page(site_data: dict, base_url: str) -> str:
    agent = site_data["agent"]
    # 404 is served from any depth, so every link must be absolute.
    return f"""{page_head(base_url, f"Sayfa bulunamadı | {agent['name']}", "Aradığınız sayfa bulunamadı.", noindex=True)}
<body id="top">
{page_header(base_url, agent)}

<main id="main" tabindex="-1">
<div class="wrap">
<div class="section-head">
<h1>Sayfa bulunamadı</h1>
<p class="lead">Aradığınız ilan satılmış ya da yayından kaldırılmış olabilir.</p>
</div>
<p><a class="btn btn-dark" href="{esc(base_url)}">Ana sayfaya dön</a> <a class="btn btn-ghost" href="{esc(base_url)}{LISTING_DIR_NAME}/">Tüm ilanlar</a></p>
</div>
</main>

{page_footer(base_url, agent)}"""


def render_sitemap(base_url: str, listing_dates: list) -> str:
    latest = max((date for _, date in listing_dates if date), default=None)
    entries = [(base_url, latest), (f"{base_url}{LISTING_DIR_NAME}/", latest), (f"{base_url}{KVKK_DIR_NAME}/", None)]
    entries += [(f"{base_url}{LISTING_DIR_NAME}/{code}/", date) for code, date in listing_dates]
    urls = "\n".join(
        f"  <url><loc>{esc(url)}</loc>{f'<lastmod>{esc(date)}</lastmod>' if date else ''}</url>" for url, date in entries
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n'


def render_robots(base_url: str) -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {base_url}sitemap.xml\n"


# --- orchestration ---------------------------------------------------------------------------
def write_if_changed(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")


def remove_stale_listing_dirs(listing_root: Path, page_codes: set) -> list:
    removed = []
    if not listing_root.is_dir():
        return removed
    for child in sorted(listing_root.iterdir()):
        if child.is_dir() and LISTING_CODE_PATTERN.fullmatch(child.name) and child.name not in page_codes:
            shutil.rmtree(child)
            removed.append(child.name)
    return removed


def build_site(output_root: Path, site_data: dict, detail_cache: dict, base_url: str) -> dict:
    """Write every generated file under output_root; returns counts for logging and tests."""
    pages = []
    for listing in site_data["listings"]:
        code = listing.get("code", "")
        if not LISTING_CODE_PATTERN.fullmatch(code):
            log.warning("Skipping listing with unexpected code %r; it cannot be a safe directory name", code)
            continue
        if not listing.get("pageUrl"):
            continue
        if code not in detail_cache:
            log.warning("Listing %s has pageUrl but no cached detail; page not generated", code)
            continue
        pages.append((listing, detail_cache[code]))
    page_codes = {listing["code"] for listing, _ in pages}
    media = existing_media(output_root)
    listing_root = output_root / LISTING_DIR_NAME
    for listing, detail in pages:
        write_if_changed(listing_root / listing["code"] / "index.html",
                         render_listing_page(listing, detail, site_data, base_url, page_codes, media))
    write_if_changed(listing_root / "index.html", render_hub_page(site_data, base_url, page_codes, media))
    write_if_changed(output_root / "sitemap.xml",
                     render_sitemap(base_url, [(listing["code"], detail.get("date")) for listing, detail in pages]))
    write_if_changed(output_root / "robots.txt", render_robots(base_url))
    write_if_changed(output_root / "404.html", render_not_found_page(site_data, base_url))
    removed = remove_stale_listing_dirs(listing_root, page_codes)
    return {"pages": len(pages), "removed": removed}


def check_index_consistency(index_path: Path, agent: dict) -> None:
    """The home page is hand-written; warn when its contact facts drift from RE/MAX's record."""
    if not index_path.exists():
        log.warning("%s not found; skipping contact consistency check", index_path)
        return
    index_html = index_path.read_text(encoding="utf-8")
    for field in ("phone", "email", "licenseNumber"):
        if agent.get(field) and agent[field] not in index_html:
            log.warning("index.html does not contain agent %s %s from data.js; update the home page", field, agent[field])


def main() -> int:
    configure_logging("build_site")
    base_url = read_base_url()
    try:
        site_data = load_site_data(DATA_PATH)
        detail_cache = load_detail_cache(DETAIL_CACHE_PATH)
    except (OSError, ValueError) as error:
        log.error("Cannot read build inputs: %s", error)
        return 1
    result = build_site(REPO_ROOT, site_data, detail_cache, base_url)
    check_index_consistency(INDEX_PATH, site_data["agent"])
    log.info("Built %d listing pages, hub, sitemap, robots and 404; removed stale pages: %s",
             result["pages"], ", ".join(result["removed"]) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
