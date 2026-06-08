#!/usr/bin/env python3
"""Scrape FUTWIZ player data and image assets from a player URL."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag


FACE_STAT_KEYS = ("PAC", "SHO", "PAS", "DRI", "DEF", "PHY", "DIV", "HAN", "KIC", "REF", "SPD", "POS")
SECTION_STOP_WORDS = {
    "total face stats",
    "total face values",
    "total stats",
    "total values",
    "playstyles+",
    "playstyles",
    "choose player chemistry",
    "chemistry styles",
    "prices",
    "evolutions",
    "comments",
    "compare",
    "history",
    "share",
}


@dataclass(frozen=True)
class ImageAsset:
    kind: str
    url: str
    alt: str = ""
    filename: str | None = None


class CloudflareChallenge(RuntimeError):
    pass


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    return value.strip("-") or "player"


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def is_cloudflare_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    title = clean_text(soup.title.get_text(" ")) if soup.title else ""
    page_text = clean_text(soup.get_text(" "))

    has_player_content = bool(
        soup.find("h1")
        and (
            "Card ID" in page_text
            or "Skill Moves" in page_text
            or "Weak Foot" in page_text
            or "Playstyles" in page_text
        )
    )
    if has_player_content:
        return False

    return (
        title == "Just a moment..."
        or "Enable JavaScript and cookies to continue" in page_text
        or ("cf_chl_opt" in html and "/cdn-cgi/challenge-platform/" in html)
    )


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=30)
    if response.status_code in {403, 429, 503} or is_cloudflare_page(response.text):
        raise CloudflareChallenge("FUTWIZ returned a Cloudflare challenge.")
    response.raise_for_status()
    return response.text


def fetch_html_with_browser(url: str) -> tuple[str, list[dict[str, str]]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Run: pip install -r requirements.txt") from exc

    print("Opening a browser because the direct request was blocked.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        try:
            page.wait_for_selector(".fc-card", timeout=15_000)
        except Exception:
            html = page.content()
            if "No Players Found" in html:
                browser.close()
                cookie_dicts = [{"name": cookie["name"], "value": cookie["value"], "domain": cookie["domain"]} for cookie in context.cookies()]
                return html, cookie_dicts
            print("Cloudflare or another interstitial still appears to be blocking the player page.")
            print("Complete it in the opened browser, wait for the FUTWIZ player page to load, then return here.")
            input("Press Enter after the real FUTWIZ page is visible...")
            page.wait_for_selector(".fc-card", timeout=60_000)
        html = page.content()
        cookies = context.cookies()
        browser.close()

    if is_cloudflare_page(html):
        raise CloudflareChallenge("The browser is still showing the Cloudflare challenge.")

    cookie_dicts = [{"name": cookie["name"], "value": cookie["value"], "domain": cookie["domain"]} for cookie in cookies]
    return html, cookie_dicts


def load_html(session: requests.Session, url: str, html_path: Path | None, browser_fallback: bool) -> str:
    if html_path:
        return html_path.read_text(encoding="utf-8")

    try:
        return fetch_html(session, url)
    except CloudflareChallenge:
        if not browser_fallback:
            raise
        html, cookies = fetch_html_with_browser(url)
        for cookie in cookies:
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
        return html


def is_listing_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return path.endswith("/players")


def inject_release_param(url: str, release_id: int) -> str:
    parsed = urlparse(url)
    query_params = [(k, v) for k, v in parse_qsl(parsed.query) if k != "release[]"]
    query_params.append(("release[]", str(release_id)))
    return urlunparse(parsed._replace(query=urlencode(query_params)))


def write_players_index(profiles: list[dict], output_dir: Path) -> None:
    players = [
        {"name": p.get("name", ""), "rating": p.get("rating"), "position": p.get("position", "")}
        for p in profiles
    ]
    (output_dir / "players.json").write_text(
        json.dumps(players, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def player_output_name(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) >= 2:
        return slugify(f"{parts[-2]}-{parts[-1]}")
    return slugify(urlparse(url).path)


def listing_page_urls(url: str, pages: int) -> list[str]:
    if pages <= 1:
        return [url]

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    start_page = int(query.get("page", "0") or "0")
    urls: list[str] = []
    for page in range(start_page, start_page + pages):
        query["page"] = str(page)
        urls.append(urlunparse(parsed._replace(query=urlencode(query))))
    return urls


def url_path_stem(url: str) -> str:
    stem = Path(urlparse(url).path).stem
    return stem or "asset"


def card_background_id(card: Tag) -> str:
    for raw in extract_style_urls(str(card.get("style", ""))):
        return url_path_stem(raw)
    return "unknown-bg"


def card_face_id(card: Tag) -> str:
    face = card.select_one("img.fc25-card__face__altimg") or card.select_one(".fc25-card__face img")
    if not face:
        return "unknown-face"
    raw = face.get("src") or face.get("data-src") or face.get("data-original") or ""
    return url_path_stem(str(raw))


def profile_id_from_url(url: str) -> str:
    if "#card-" in url:
        return ""
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[-1] if parts and parts[-1].isdigit() else ""


def listing_output_dir(output_dir: Path, card: Tag, _source_url: str, fallback_index: int) -> Path:
    summary = extract_card_summary(card)
    player_slug = slugify(str(summary.get("name") or summary.get("card_name") or f"player-{fallback_index}"))
    return next_player_version_dir(output_dir / player_slug)


def next_player_version_dir(player_dir: Path) -> Path:
    highest_version = 0
    existing_variants = 0

    if player_dir.exists():
        for child in player_dir.iterdir():
            if not child.is_dir():
                continue
            existing_variants += 1
            match = re.fullmatch(r"v(\d+)", child.name)
            if match:
                highest_version = max(highest_version, int(match.group(1)))

    next_version = max(highest_version, existing_variants) + 1
    return player_dir / f"v{next_version}"


def extract_player_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    player_path = re.compile(r"/fc\d+/player/[^/?#]+/\d+/?$")

    for anchor in soup.select("a[href]"):
        absolute = urljoin(base_url, str(anchor.get("href")))
        parsed = urlparse(absolute)
        normalized = parsed._replace(query="", fragment="").geturl()
        if not player_path.search(urlparse(normalized).path):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(normalized)

    return links


def extract_labeled_info(soup: BeautifulSoup) -> dict[str, str]:
    lines = [clean_text(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    labels = {
        "Name",
        "Version",
        "Club",
        "League",
        "Nationality",
        "Pos.",
        "Alt Pos.",
        "Skill Moves",
        "Weak Foot",
        "Foot",
        "Age",
        "Height",
        "Weight",
        "Body Type",
        "Shirt Number",
        "Player ID",
        "Card ID",
        "Added",
    }
    info: dict[str, str] = {}
    for index, line in enumerate(lines[:-1]):
        if line in labels and line.rstrip(".") not in info:
            info[line.rstrip(".")] = lines[index + 1]
    return info


def extract_title_fields(soup: BeautifulSoup, info: dict[str, str]) -> dict[str, object]:
    title = clean_text(soup.title.get_text(" ")) if soup.title else ""
    h1 = soup.find("h1")
    name = info.get("Name") or (clean_text(h1.get_text(" ")) if h1 else "")

    lines = [clean_text(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    rating = next((line for line in lines if line.isdigit() and 1 <= int(line) <= 99), "")

    position = info.get("Pos", "")
    if not position and rating:
        try:
            position = lines[lines.index(rating) + 1]
        except (ValueError, IndexError):
            position = ""

    version = info.get("Version", "")
    if not version:
        match = re.search(r"fc26\s+(.+?)\s+-\s+rated", title, re.I)
        if match:
            version = match.group(1).strip()

    return {
        "name": name,
        "version": version,
        "rating": int(rating) if rating else None,
        "position": position,
    }


def extract_stats(soup: BeautifulSoup) -> tuple[dict[str, int], dict[str, dict[str, object]]]:
    lines = [clean_text(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    face_stats: dict[str, int] = {}
    detailed_stats: dict[str, dict[str, object]] = {}

    index = 0
    while index < len(lines) - 1:
        key = lines[index]
        if key not in FACE_STAT_KEYS or not lines[index + 1].isdigit():
            index += 1
            continue

        value = int(lines[index + 1])
        face_stats.setdefault(key, value)
        section: dict[str, object] = {"value": value, "attributes": {}}
        attributes = section["attributes"]
        index += 2

        while index < len(lines):
            current = lines[index]
            lower = current.lower()
            if current in FACE_STAT_KEYS or lower in SECTION_STOP_WORDS:
                break
            if index + 1 < len(lines) and lines[index + 1].isdigit():
                attributes[current] = int(lines[index + 1])  # type: ignore[index]
                index += 2
                continue
            if current == "AcceleRATE" and index + 1 < len(lines):
                section["accelerate"] = lines[index + 1]
                index += 2
                continue
            index += 1

        detailed_stats.setdefault(key, section)

    return face_stats, detailed_stats


def classify_image(tag: Tag, url: str, alt: str) -> str:
    haystack = " ".join([alt, " ".join(tag.get("class", [])), str(tag.get("id", "")), url]).lower()
    if any(value in haystack for value in ("background", "cardbg", "card-bg", "card design", "/bg/")):
        return "background"
    if any(value in haystack for value in ("card image", "card-small", "card-large", "card.png", "/cards/")):
        return "card"
    if any(value in haystack for value in ("face", "dynamic", "portrait", "headshot")):
        return "player_image"
    if "badge" in haystack:
        return "club_badge"
    if "flag" in haystack:
        return "nationality_flag"
    if "league" in haystack:
        return "league_logo"
    return "other"


def extract_style_urls(style: str) -> list[str]:
    return re.findall(r"url\((?:'|\")?([^)'\"\s]+)(?:'|\")?\)", style)


def collect_images(soup: BeautifulSoup, base_url: str) -> list[ImageAsset]:
    assets: list[ImageAsset] = []
    seen: set[str] = set()

    card = soup.select_one(".fc-card")
    if card and card.get("style"):
        for raw in extract_style_urls(str(card.get("style", ""))):
            url = urljoin(base_url, raw)
            if url in seen or url.startswith("data:"):
                continue
            seen.add(url)
            assets.append(ImageAsset(kind="background", url=url))

    face = soup.select_one("img.fc25-card__face__altimg")
    if not face:
        face = soup.select_one(".fc25-card__face img")
    if face:
        raw = face.get("src") or face.get("data-src") or face.get("data-original")
        if not raw:
            return assets
        url = urljoin(base_url, str(raw))
        if url not in seen and not url.startswith("data:"):
            seen.add(url)
            alt = clean_text(str(face.get("alt", "")))
            assets.append(ImageAsset(kind="player_image", url=url, alt=alt))

    return assets


def write_card_html(card: Tag | None, output_dir: Path) -> dict[str, str | None]:
    card = clean_card_for_export(card)
    if not card:
        return {"selector": ".fc-card", "html": None, "filename": None, "render_filename": None}

    filename = "card.html"
    (output_dir / filename).write_text(str(card), encoding="utf-8")
    return {"selector": ".fc-card", "html": str(card), "filename": filename, "render_filename": None}


def extract_card_html(soup: BeautifulSoup, output_dir: Path) -> dict[str, str | None]:
    return write_card_html(soup.select_one(".fc-card"), output_dir)


def clean_card_for_export(card: Tag | None) -> Tag | None:
    if not card:
        return None

    card_copy = BeautifulSoup(str(card), "html.parser").select_one(".fc-card")
    if not card_copy:
        return None

    for selector in (
        ".fc25-card__playstyles",
        ".fc25-card__extra-positions",
        ".fc25-card__player-stats",
    ):
        for element in card_copy.select(selector):
            element.decompose()

    return card_copy


def absolute_attrs(tag: Tag, base_url: str) -> None:
    for attr in ("href", "src"):
        if tag.get(attr):
            tag[attr] = urljoin(base_url, str(tag[attr]))


def css_background_value(value: str, image_size: str) -> str:
    value = value.strip()
    lower = value.lower()
    if lower.startswith(("url(", "#", "rgb(", "rgba(", "hsl(", "hsla(")) or "gradient(" in lower:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'url("{escaped}") center / {image_size} no-repeat'


def apply_card_background(card: Tag, background: str | None) -> None:
    if not background:
        return

    style = str(card.get("style", "")).rstrip()
    if style and not style.endswith(";"):
        style += ";"
    style += f" background: {css_background_value(background, 'contain')};"
    card["style"] = style


def extract_card_render_page(
    soup: BeautifulSoup,
    base_url: str,
    output_dir: Path,
    card: Tag | None = None,
    page_background: str = "#111",
    card_background: str | None = None,
) -> str | None:
    card = clean_card_for_export(card or soup.select_one(".fc-card"))
    if not card:
        return None
    apply_card_background(card, card_background)

    head_parts: list[str] = []
    for tag in soup.find_all(["link", "style"]):
        if tag.name == "link":
            rel = " ".join(tag.get("rel", [])).lower()
            href = tag.get("href")
            if not href or ("stylesheet" not in rel and "preload" not in rel and "icon" not in rel):
                continue
            tag = Tag(name="link", attrs=dict(tag.attrs))
            absolute_attrs(tag, base_url)
            head_parts.append(str(tag))
        elif tag.name == "style":
            head_parts.append(str(tag))

    for tag in card.find_all(["img", "script", "link"]):
        absolute_attrs(tag, base_url)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FUTWIZ Card Render</title>
  {"".join(head_parts)}
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: {css_background_value(page_background, 'cover')};
    }}
  </style>
</head>
<body>
  <svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">
    <defs>
      <marker id="pos" markerWidth="1" markerHeight="1" refX="0" refY="0"></marker>
    </defs>
  </svg>
  {card}
</body>
</html>
"""
    filename = "card_render.html"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / filename).write_text(html, encoding="utf-8")
    return filename


def extension_from_response(url: str, response: requests.Response) -> str:
    suffix = Path(urlparse(url).path).suffix
    if suffix:
        return suffix
    content_type = response.headers.get("content-type", "").split(";")[0]
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }.get(content_type, ".img")


def download_images(session: requests.Session, output_dir: Path, assets: list[ImageAsset]) -> list[ImageAsset]:
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    counters: dict[str, int] = {}
    saved_assets: list[ImageAsset] = []

    for asset in assets:
        counters[asset.kind] = counters.get(asset.kind, 0) + 1
        try:
            response = session.get(asset.url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Skipping image {asset.url}: {exc}", file=sys.stderr)
            saved_assets.append(asset)
            continue

        filename = f"{asset.kind}-{counters[asset.kind]}{extension_from_response(asset.url, response)}"
        path = image_dir / filename
        path.write_bytes(response.content)
        saved_assets.append(ImageAsset(asset.kind, asset.url, asset.alt, str(path.relative_to(output_dir))))

    return saved_assets


def extract_card_face_stats(card: Tag) -> dict[str, int]:
    labels = [clean_text(tag.get_text(" ")) for tag in card.select(".fc25-card__attribute-label")]
    values = [clean_text(tag.get_text(" ")) for tag in card.select(".fc25-card__attribute-value")]
    stats: dict[str, int] = {}
    for label, value in zip(labels, values):
        if label and value.isdigit():
            stats[label] = int(value)
    return stats


def extract_card_summary(card: Tag) -> dict[str, object]:
    rating_text = clean_text(card.select_one(".fc25-card__rating").get_text(" ")) if card.select_one(".fc25-card__rating") else ""
    position = clean_text(card.select_one(".fc25-card__position").get_text(" ")) if card.select_one(".fc25-card__position") else ""
    name = clean_text(card.select_one(".fc25-card__name").get_text(" ")) if card.select_one(".fc25-card__name") else ""
    face = card.select_one("img.fc25-card__face__altimg") or card.select_one(".fc25-card__face img")
    full_name = clean_text(str(face.get("alt", ""))) if face and face.get("alt") else name
    return {
        "name": full_name,
        "card_name": name,
        "version": "",
        "rating": int(rating_text) if rating_text.isdigit() else None,
        "position": position,
    }


def find_card_profile_url(card: Tag, base_url: str, fallback_index: int) -> str:
    player_path = re.compile(r"/fc\d+/player/[^/?#]+/\d+/?$")
    for parent in [card.find_parent("a"), *card.find_parents()]:
        if not parent:
            continue
        anchors = [parent] if parent.name == "a" else parent.select("a[href]")
        for anchor in anchors:
            href = anchor.get("href")
            if not href:
                continue
            absolute = urljoin(base_url, str(href))
            normalized = urlparse(absolute)._replace(query="", fragment="").geturl()
            if player_path.search(urlparse(normalized).path):
                return normalized
    return f"{base_url}#card-{fallback_index}"


def scrape_listing_card(
    session: requests.Session,
    listing_soup: BeautifulSoup,
    listing_url: str,
    card: Tag,
    output_dir: Path,
    source_url: str,
    download: bool,
    render_output_dir: Path | None = None,
    render_page_background: str = "#111",
    render_card_background: str | None = None,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fragment_soup = BeautifulSoup(str(card), "html.parser")
    assets = collect_images(fragment_soup, listing_url)
    card_html = write_card_html(card, output_dir)
    render_filename = extract_card_render_page(listing_soup, listing_url, output_dir, card)
    if render_output_dir:
        extract_card_render_page(
            listing_soup,
            listing_url,
            render_output_dir,
            card,
            page_background=render_page_background,
            card_background=render_card_background,
        )
    card_html["render_filename"] = render_filename
    if download:
        assets = download_images(session, output_dir, assets)

    face_stats = extract_card_face_stats(card)
    data: dict[str, object] = {
        "source_url": source_url,
        **extract_card_summary(card),
        "profile": {},
        "face_stats": face_stats,
        "detailed_stats": {},
        "card_html": card_html,
        "images": [asdict(asset) for asset in assets],
    }
    (output_dir / "player.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def scrape_player_html(
    session: requests.Session,
    url: str,
    output_dir: Path,
    html: str,
    download: bool,
    render_output_dir: Path | None = None,
    render_page_background: str = "#111",
    render_card_background: str | None = None,
) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")

    info = extract_labeled_info(soup)
    face_stats, detailed_stats = extract_stats(soup)
    assets = collect_images(soup, url)

    output_dir.mkdir(parents=True, exist_ok=True)
    card_html = extract_card_html(soup, output_dir)
    render_filename = extract_card_render_page(soup, url, output_dir)
    if render_output_dir:
        extract_card_render_page(
            soup,
            url,
            render_output_dir,
            page_background=render_page_background,
            card_background=render_card_background,
        )
    card_html["render_filename"] = render_filename
    if download:
        assets = download_images(session, output_dir, assets)

    data: dict[str, object] = {
        "source_url": url,
        **extract_title_fields(soup, info),
        "profile": info,
        "face_stats": face_stats,
        "detailed_stats": detailed_stats,
        "card_html": card_html,
        "images": [asdict(asset) for asset in assets],
    }
    (output_dir / "player.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def scrape_player(
    session: requests.Session,
    url: str,
    output_dir: Path,
    html_path: Path | None,
    browser_fallback: bool,
    download: bool,
    render_output_dir: Path | None = None,
    render_page_background: str = "#111",
    render_card_background: str | None = None,
) -> dict[str, object]:
    html = load_html(session, url, html_path, browser_fallback)
    return scrape_player_html(
        session,
        url,
        output_dir,
        html,
        download,
        render_output_dir=render_output_dir,
        render_page_background=render_page_background,
        render_card_background=render_card_background,
    )


def scrape(
    url: str,
    output_dir: Path,
    html_path: Path | None,
    browser_fallback: bool,
    download: bool,
    render_output_dir: Path | None = None,
    render_page_background: str = "#111",
    render_card_background: str | None = None,
) -> dict[str, object]:
    session = make_session()
    return scrape_player(
        session,
        url,
        output_dir,
        html_path,
        browser_fallback,
        download,
        render_output_dir=render_output_dir,
        render_page_background=render_page_background,
        render_card_background=render_card_background,
    )


def scrape_listing_page(
    session: requests.Session,
    url: str,
    output_dir: Path,
    html_path: Path | None,
    browser_fallback: bool,
    download: bool,
    render_output_dir: Path | None = None,
    render_page_background: str = "#111",
    render_card_background: str | None = None,
) -> dict[str, object]:
    html = load_html(session, url, html_path, browser_fallback)
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".fc-card")
    if not cards:
        return {
            "source_url": url,
            "profiles_found": 0,
            "profiles_scraped": 0,
            "profiles_failed": 0,
            "profiles": [],
            "failures": [],
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    for index, card in enumerate(cards, start=1):
        player_url = find_card_profile_url(card, url, index)
        summary = extract_card_summary(card)
        player_dir = listing_output_dir(output_dir, card, player_url, index)
        custom_render_dir = render_output_dir / player_dir.relative_to(output_dir) if render_output_dir else None
        print(f"[{index}/{len(cards)}] Saving rendered card {summary.get('name') or player_dir.name}")
        try:
            data = scrape_listing_card(
                session,
                soup,
                url,
                card,
                player_dir,
                player_url,
                download,
                render_output_dir=custom_render_dir,
                render_page_background=render_page_background,
                render_card_background=render_card_background,
            )
        except Exception as exc:
            print(f"Failed {player_url}: {exc}", file=sys.stderr)
            failures.append({"url": player_url, "error": str(exc)})
            continue

        results.append(
            {
                "url": player_url,
                "output": str(player_dir.relative_to(output_dir)),
                "name": str(data.get("name") or ""),
                "rating": data.get("rating"),
                "position": str(data.get("position") or ""),
            }
        )

    manifest: dict[str, object] = {
        "source_url": url,
        "profiles_found": len(cards),
        "profiles_scraped": len(results),
        "profiles_failed": len(failures),
        "profiles": results,
        "failures": failures,
    }
    return manifest


def scrape_listing(
    url: str,
    output_dir: Path,
    html_path: Path | None,
    browser_fallback: bool,
    download: bool,
    pages: int = 1,
    render_output_dir: Path | None = None,
    render_page_background: str = "#111",
    render_card_background: str | None = None,
) -> dict[str, object]:
    if html_path and pages > 1:
        raise RuntimeError("--html can only be used with one listing page at a time.")

    session = make_session()
    output_dir.mkdir(parents=True, exist_ok=True)
    page_manifests: list[dict[str, object]] = []
    all_profiles: list[dict[str, object]] = []
    all_failures: list[dict[str, str]] = []

    urls = listing_page_urls(url, pages)
    for page_index, page_url in enumerate(urls, start=1):
        print(f"Scraping listing page {page_index}/{len(urls)}: {page_url}")
        manifest = scrape_listing_page(
            session,
            page_url,
            output_dir,
            html_path,
            browser_fallback,
            download,
            render_output_dir=render_output_dir,
            render_page_background=render_page_background,
            render_card_background=render_card_background,
        )
        if not manifest["profiles"]:
            print(f"No players found on page {page_index}, stopping pagination for this release type.")
            break
        page_manifests.append(manifest)
        all_profiles.extend(manifest["profiles"])  # type: ignore[arg-type]
        all_failures.extend(manifest["failures"])  # type: ignore[arg-type]

    combined: dict[str, object] = {
        "source_url": url,
        "pages_requested": len(urls),
        "page_urls": urls,
        "profiles_found": sum(int(manifest["profiles_found"]) for manifest in page_manifests),
        "profiles_scraped": len(all_profiles),
        "profiles_failed": len(all_failures),
        "profiles": all_profiles,
        "failures": all_failures,
        "pages": page_manifests,
    }
    (output_dir / "index.json").write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    return combined


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape a FUTWIZ FC player page or players listing page.")
    parser.add_argument("url", help="FUTWIZ player URL")
    parser.add_argument("-o", "--output", default="output", help="Directory for player.json and images/")
    parser.add_argument("--html", type=Path, help="Parse a saved HTML page instead of fetching")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser if Cloudflare appears")
    parser.add_argument("--no-download", action="store_true", help="Save image URLs only")
    parser.add_argument("--pages", type=int, default=1, help="For listing URLs, scrape this many consecutive pages (per release type if --releases is used)")
    parser.add_argument(
        "--releases", nargs="+", type=int, metavar="ID",
        help="Scrape specific release types (e.g. --releases 25 111). "
             "Each type is scraped from page 0 to --pages-1 and saved in a separate folder.",
    )
    parser.add_argument("--render-output", type=Path, help="Optional separate directory for customized card_render.html files")
    parser.add_argument(
        "--render-page-background",
        default="#111",
        help="CSS color/image/gradient for the HTML render page background",
    )
    parser.add_argument(
        "--render-card-background",
        help="CSS color/image/gradient to apply to the exported card in the separate render output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.releases:
            base_output = Path(args.output)
            base_output.mkdir(parents=True, exist_ok=True)
            for release_id in args.releases:
                release_url = inject_release_param(args.url, release_id)
                release_dir = base_output / f"release_{release_id}"
                print(f"\n=== Scraping release {release_id} ===")
                data = scrape_listing(
                    url=release_url,
                    output_dir=release_dir,
                    html_path=args.html,
                    browser_fallback=not args.no_browser,
                    download=not args.no_download,
                    pages=args.pages,
                    render_output_dir=args.render_output,
                    render_page_background=args.render_page_background,
                    render_card_background=args.render_card_background,
                )
                profiles = data.get("profiles", [])
                write_players_index(profiles, release_dir)
                print(f"Release {release_id}: {data.get('profiles_scraped')}/{data.get('profiles_found')} profiles scraped")
                print(f"  Saved to {release_dir}")
                print(f"  Player index: {release_dir / 'players.json'}")
            return 0

        if is_listing_url(args.url):
            data = scrape_listing(
                url=args.url,
                output_dir=Path(args.output),
                html_path=args.html,
                browser_fallback=not args.no_browser,
                download=not args.no_download,
                pages=args.pages,
                render_output_dir=args.render_output,
                render_page_background=args.render_page_background,
                render_card_background=args.render_card_background,
            )
        else:
            data = scrape(
                url=args.url,
                output_dir=Path(args.output),
                html_path=args.html,
                browser_fallback=not args.no_browser,
                download=not args.no_download,
                render_output_dir=args.render_output,
                render_page_background=args.render_page_background,
                render_card_background=args.render_card_background,
            )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if is_listing_url(args.url):
        print(f"Saved listing scrape manifest to {Path(args.output) / 'index.json'}")
        print(f"Profiles scraped: {data.get('profiles_scraped')} / {data.get('profiles_found')}")
        if args.render_output:
            print(f"Saved customized renders to {args.render_output}")
    else:
        print(f"Saved {data.get('name') or 'player'} to {Path(args.output) / 'player.json'}")
        print(f"Images discovered: {len(data.get('images', []))}")
        if args.render_output:
            print(f"Saved customized render to {args.render_output / 'card_render.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
