#!/usr/bin/env python3
"""Create a copied card_render.html with a different card background."""

from __future__ import annotations

import argparse
import html as html_lib
import os
import re
from pathlib import Path


CARD_BACKGROUND_RE = re.compile(
    r'(<div\s+class=["\'][^"\']*\bfc-card\b[^"\']*["\'][^>]*?\bstyle=["\'][^"\']*?'
    r'background-image:\s*url\()([^)]*)(\)[^"\']*["\'])',
    re.S,
)
HEAD_CLOSE_RE = re.compile(r"</head>", re.I)
BODY_CONTENT_RE = re.compile(r"<body[^>]*>(.*)</body>", re.I | re.S)
CARD_OPEN_RE = re.compile(r"<div\s+class=[\"'][^\"']*\bfc-card\b[^\"']*[\"'][^>]*>", re.I | re.S)
DIV_TAG_RE = re.compile(r"</?div\b[^>]*>", re.I)
IMAGE_EXTENSIONS = {".avif", ".gif", ".jpg", ".jpeg", ".png", ".webp"}
UNSAFE_CSS_VALUE_RE = re.compile(r"[;{}<>]")

CONTENT_SCALE_CSS = """
  <style>
    :root {
      --render-content-scale: SCALE_VALUE;
    }

    .fc-card > .fc25-card__inner {
      position: absolute;
      inset: 0;
      transform: scale(var(--render-content-scale));
      transform-origin: center center;
    }

COLOR_RULES
  </style>
"""

SHOWCASE_CSS = """
  <style>
    :root {
      --render-content-scale: SCALE_VALUE;
    }

    body {
      min-height: 100vh;
      margin: 0;
      background: #111;
      color: #f5f5f5;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .showcase {
      width: min(1400px, calc(100% - 48px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }

    .showcase__title {
      margin: 0 0 24px;
      font-size: 24px;
      font-weight: 700;
    }

    .showcase__grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 28px;
      align-items: start;
    }

    .showcase__item {
      display: grid;
      gap: 10px;
      justify-items: center;
    }

    .showcase__card {
      position: relative;
      width: 220px;
      height: 306px;
    }

    .showcase__card .fc-card {
      position: absolute;
      inset: 0;
    }

    .showcase__card .fc-card > .fc25-card__inner {
      position: absolute;
      inset: 0;
      transform: scale(var(--render-content-scale));
      transform-origin: center center;
    }

COLOR_RULES

    .showcase__label {
      max-width: 220px;
      overflow: hidden;
      color: #ddd;
      font-size: 13px;
      line-height: 1.3;
      text-align: center;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  </style>
"""


def slugify_player(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-")


def resolve_player_dir(source_root: Path, player: str, variant: str | None) -> Path:
    player_path = Path(player)
    if player_path.exists():
        if (player_path / "card_render.html").exists():
            return player_path
        if player_path.is_dir():
            return resolve_variant_dir(player_path, variant)

    player_dir = source_root / slugify_player(player)
    if not player_dir.exists():
        raise FileNotFoundError(f"Player folder not found: {player_dir}")

    return resolve_variant_dir(player_dir, variant)


def release_sort_key(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"release_(\d+)", path.name)
    if match:
        return (int(match.group(1)), path.name)
    return (-1, path.name)


def resolve_release_dir(source_root: Path, release: str | None) -> Path | None:
    if release is None:
        return None

    release_name = release if release.startswith("release_") else f"release_{release}"
    release_dir = source_root / release_name
    if not release_dir.exists():
        raise FileNotFoundError(f"Release folder not found: {release_dir}")
    return release_dir


def resolve_player_dir_from_releases(
    source_root: Path, player: str, variant: str | None, release: str | None
) -> Path:
    player_path = Path(player)
    if player_path.exists():
        if (player_path / "card_render.html").exists():
            return player_path
        if player_path.is_dir():
            return resolve_variant_dir(player_path, variant)

    explicit_release_dir = resolve_release_dir(source_root, release)
    if explicit_release_dir is not None:
        return resolve_player_dir(explicit_release_dir, player, variant)

    player_slug = slugify_player(player)
    direct_player_dir = source_root / player_slug
    if direct_player_dir.exists():
        return resolve_variant_dir(direct_player_dir, variant)

    release_dirs = sorted(
        (child for child in source_root.iterdir() if child.is_dir() and child.name.startswith("release_")),
        key=release_sort_key,
        reverse=True,
    )
    matches = [release_dir / player_slug for release_dir in release_dirs if (release_dir / player_slug).exists()]
    if not matches:
        raise FileNotFoundError(
            f"Player folder not found for {player!r}. Checked {direct_player_dir} and release_* folders under {source_root}"
        )

    return resolve_variant_dir(matches[0], variant)


def resolve_variant_dir(player_dir: Path, variant: str | None) -> Path:
    if variant:
        variant_dir = player_dir / variant
        if not variant_dir.exists():
            raise FileNotFoundError(f"Variant folder not found: {variant_dir}")
        if not (variant_dir / "card_render.html").exists():
            raise FileNotFoundError(f"Missing card_render.html: {variant_dir}")
        return variant_dir

    if (player_dir / "card_render.html").exists():
        return player_dir

    variant_dirs = sorted(
        child for child in player_dir.iterdir() if child.is_dir() and (child / "card_render.html").exists()
    )
    if not variant_dirs:
        raise FileNotFoundError(f"No card_render.html found under: {player_dir}")

    return variant_dirs[0]


def html_relative_path(from_dir: Path, target: Path) -> str:
    relative = os.path.relpath(target.resolve(), from_dir.resolve())
    return relative.replace(os.sep, "/")


def destination_dir_for_source(source_dir: Path, source_root: Path, render_root: Path) -> Path:
    try:
        return render_root / source_dir.relative_to(source_root)
    except ValueError:
        return render_root / source_dir.name


def validate_css_color(value: str | None, option_name: str) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError(f"{option_name} cannot be empty")
    if UNSAFE_CSS_VALUE_RE.search(value):
        raise ValueError(f"{option_name} contains unsupported CSS characters")
    return value


def text_color_rules(top_text_color: str | None, bottom_text_color: str | None) -> str:
    rules = []
    if top_text_color:
        rules.append(
            f"""
    .fc-card .fc25-card__rating,
    .fc-card .fc25-card__position,
    .fc-card .fc25-card__chemstyle,
    .fc-card .fc25-card__chemstyle i,
    .fc-card .fc25-card__foot,
    .fc-card .fc25-card__preferred-foot {{
      color: {top_text_color} !important;
    }}"""
        )
    if bottom_text_color:
        rules.append(
            f"""
    .fc-card .fc25-card__name,
    .fc-card .fc25-card__attributes,
    .fc-card .fc25-card__attribute-label,
    .fc-card .fc25-card__attribute-value {{
      color: {bottom_text_color} !important;
    }}"""
        )
    return "\n".join(rules)


def inject_card_scale(html: str, scale: float, top_text_color: str | None, bottom_text_color: str | None) -> str:
    scale_css = (
        CONTENT_SCALE_CSS.replace("SCALE_VALUE", f"{scale:g}")
        .replace("COLOR_RULES", text_color_rules(top_text_color, bottom_text_color))
    )
    html, count = HEAD_CLOSE_RE.subn(f"{scale_css}</head>", html, count=1)
    if count != 1:
        raise RuntimeError("Expected one </head> tag while adding render scale CSS")
    return html


def background_label(background: Path) -> str:
    return background.stem.replace("_", " ").replace("-", " ").title()


def list_backgrounds(background_dir: Path) -> list[Path]:
    if not background_dir.exists():
        raise FileNotFoundError(f"Background directory not found: {background_dir}")
    if not background_dir.is_dir():
        raise NotADirectoryError(f"Background path is not a directory: {background_dir}")

    backgrounds = sorted(
        child for child in background_dir.iterdir() if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not backgrounds:
        raise FileNotFoundError(f"No background images found in: {background_dir}")
    return backgrounds


def extract_card_fragment(render_html: str) -> str:
    body_match = BODY_CONTENT_RE.search(render_html)
    body = body_match.group(1) if body_match else render_html

    card_match = CARD_OPEN_RE.search(body)
    if not card_match:
        raise RuntimeError("Expected one .fc-card element while creating showcase")

    depth = 0
    for div_match in DIV_TAG_RE.finditer(body, card_match.start()):
        is_close = div_match.group(0).startswith("</")
        depth += -1 if is_close else 1
        if depth == 0:
            return body[card_match.start() : div_match.end()]

    raise RuntimeError("Could not find the closing tag for the .fc-card element")


def replace_card_background(html: str, background_ref: str) -> str:
    replacement = rf'\1"{background_ref}"\3'
    html, count = CARD_BACKGROUND_RE.subn(replacement, html, count=1)
    if count != 1:
        raise RuntimeError(f"Expected one card background-image, replaced {count}")
    return html


def inject_showcase_css(html: str, scale: float, top_text_color: str | None, bottom_text_color: str | None) -> str:
    showcase_css = (
        SHOWCASE_CSS.replace("SCALE_VALUE", f"{scale:g}")
        .replace("COLOR_RULES", text_color_rules(top_text_color, bottom_text_color))
    )
    html, count = HEAD_CLOSE_RE.subn(f"{showcase_css}</head>", html, count=1)
    if count != 1:
        raise RuntimeError("Expected one </head> tag while adding showcase CSS")
    return html


def create_render(
    source_render: Path,
    destination_render: Path,
    background: Path,
    scale: float,
    top_text_color: str | None,
    bottom_text_color: str | None,
) -> int:
    html = source_render.read_text(encoding="utf-8")
    background_ref = html_relative_path(destination_render.parent, background)
    html = replace_card_background(html, background_ref)

    html = inject_card_scale(html, scale, top_text_color, bottom_text_color)
    destination_render.parent.mkdir(parents=True, exist_ok=True)
    destination_render.write_text(html, encoding="utf-8")
    return 1


def create_showcase(
    source_render: Path,
    destination_render: Path,
    backgrounds: list[Path],
    player_label: str,
    scale: float,
    top_text_color: str | None,
    bottom_text_color: str | None,
) -> int:
    html = source_render.read_text(encoding="utf-8")
    card_fragment = extract_card_fragment(html)
    cards = []

    for background in backgrounds:
        background_ref = html_relative_path(destination_render.parent, background)
        card_html = replace_card_background(card_fragment, background_ref)
        label = html_lib.escape(background_label(background))
        cards.append(
            f'      <section class="showcase__item">\n'
            f'        <div class="showcase__card">{card_html}</div>\n'
            f'        <div class="showcase__label" title="{label}">{label}</div>\n'
            f"      </section>"
        )

    title = html_lib.escape(player_label)
    showcase_body = (
        "<body>\n"
        '  <main class="showcase">\n'
        f'    <h1 class="showcase__title">{title} Background Showcase</h1>\n'
        '    <div class="showcase__grid">\n'
        + "\n".join(cards)
        + "\n"
        "    </div>\n"
        "  </main>\n"
        "</body>"
    )

    html = BODY_CONTENT_RE.sub(showcase_body, html, count=1)
    html = inject_showcase_css(html, scale, top_text_color, bottom_text_color)
    destination_render.parent.mkdir(parents=True, exist_ok=True)
    destination_render.write_text(html, encoding="utf-8")
    return len(backgrounds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a copied card_render.html with a new card background.")
    parser.add_argument("player", help="Player name, player slug, or path to a player/variant folder")
    parser.add_argument("background", type=Path, nargs="?", help="Image file to use as the card background")
    parser.add_argument("--variant", help="Variant folder name when the player has multiple saved cards")
    parser.add_argument("--release", help="Release folder or number, for example release_111 or 111")
    parser.add_argument("--source-root", type=Path, default=Path("output"))
    parser.add_argument("--render-root", type=Path, default=Path("new_html_renders"))
    parser.add_argument(
        "--showcase",
        action="store_true",
        help="Create showcase.html with the player rendered against every image in the background directory",
    )
    parser.add_argument(
        "--background-dir",
        type=Path,
        help="Directory of backgrounds to use with --showcase. Defaults to the positional background's directory or rarity_backgrounds.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Scale the rendered card contents while leaving the card background unchanged",
    )
    parser.add_argument(
        "--top-text-color",
        help="Color for the top-left text group: rating, position, and foot/chemstyle text",
    )
    parser.add_argument(
        "--bottom-text-color",
        help="Color for the bottom text group: player name and sub ratings",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.scale <= 0:
        raise ValueError("--scale must be greater than 0")

    background = args.background
    if not args.showcase and background is None:
        raise ValueError("background is required unless --showcase is used")
    if not args.showcase and not background.exists():
        raise FileNotFoundError(f"Background not found: {background}")
    top_text_color = validate_css_color(args.top_text_color, "--top-text-color")
    bottom_text_color = validate_css_color(args.bottom_text_color, "--bottom-text-color")

    source_dir = resolve_player_dir_from_releases(args.source_root, args.player, args.variant, args.release)
    destination_dir = destination_dir_for_source(source_dir, args.source_root, args.render_root)
    if args.showcase:
        background_dir = args.background_dir or (background.parent if background is not None else Path("rarity_backgrounds"))
        destination_render = destination_dir / "showcase.html"
        count = create_showcase(
            source_dir / "card_render.html",
            destination_render,
            list_backgrounds(background_dir),
            args.player,
            args.scale,
            top_text_color,
            bottom_text_color,
        )
        print(f"{destination_render} ({count} backgrounds)")
        return 0

    destination_render = destination_dir / "card_render.html"
    create_render(
        source_dir / "card_render.html",
        destination_render,
        background,
        args.scale,
        top_text_color,
        bottom_text_color,
    )
    print(destination_render)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
