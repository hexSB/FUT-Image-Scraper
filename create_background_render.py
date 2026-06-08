#!/usr/bin/env python3
"""Create a copied card_render.html with a different card background."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


CARD_BACKGROUND_RE = re.compile(
    r'(<div\s+class=["\'][^"\']*\bfc-card\b[^"\']*["\'][^>]*?\bstyle=["\'][^"\']*?'
    r'background-image:\s*url\()([^)]*)(\)[^"\']*["\'])',
    re.S,
)
HEAD_CLOSE_RE = re.compile(r"</head>", re.I)

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


def inject_card_scale(html: str, scale: float) -> str:
    scale_css = CONTENT_SCALE_CSS.replace("SCALE_VALUE", f"{scale:g}")
    html, count = HEAD_CLOSE_RE.subn(f"{scale_css}</head>", html, count=1)
    if count != 1:
        raise RuntimeError("Expected one </head> tag while adding render scale CSS")
    return html


def create_render(source_render: Path, destination_render: Path, background: Path, scale: float) -> int:
    html = source_render.read_text(encoding="utf-8")
    background_ref = html_relative_path(destination_render.parent, background)
    replacement = rf'\1"{background_ref}"\3'
    html, count = CARD_BACKGROUND_RE.subn(replacement, html, count=1)
    if count != 1:
        raise RuntimeError(f"Expected one card background-image in {source_render}, replaced {count}")

    html = inject_card_scale(html, scale)
    destination_render.parent.mkdir(parents=True, exist_ok=True)
    destination_render.write_text(html, encoding="utf-8")
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a copied card_render.html with a new card background.")
    parser.add_argument("player", help="Player name, player slug, or path to a player/variant folder")
    parser.add_argument("background", type=Path, help="Image file to use as the card background")
    parser.add_argument("--variant", help="Variant folder name when the player has multiple saved cards")
    parser.add_argument("--release", help="Release folder or number, for example release_111 or 111")
    parser.add_argument("--source-root", type=Path, default=Path("output"))
    parser.add_argument("--render-root", type=Path, default=Path("new_html_renders"))
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Scale the rendered card contents while leaving the card background unchanged",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.scale <= 0:
        raise ValueError("--scale must be greater than 0")

    background = args.background
    if not background.exists():
        raise FileNotFoundError(f"Background not found: {background}")

    source_dir = resolve_player_dir_from_releases(args.source_root, args.player, args.variant, args.release)
    destination_dir = destination_dir_for_source(source_dir, args.source_root, args.render_root)
    destination_render = destination_dir / "card_render.html"

    create_render(source_dir / "card_render.html", destination_render, background, args.scale)
    print(destination_render)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
