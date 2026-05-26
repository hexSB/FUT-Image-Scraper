# FUTWIZ Image Scraper

A Python scraper for FUTWIZ FC 26 player cards.

The scraper can:

- scrape one FUTWIZ player profile page
- scrape all rendered player cards on a FUTWIZ listing page
- scrape multiple consecutive listing pages
- save card metadata as JSON
- save the cleaned card HTML needed to recreate the card
- save a standalone `card_render.html` preview page
- download the card background image and player face image

For listing pages, the scraper uses the player cards already rendered on the listing page. It does not open every individual player profile.

## Requirements

- Python 3.9+
- `pip`
- Playwright Chromium browser

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

If your shell cannot find `python`, use `python3` instead.

## Usage

Scrape one player profile:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/player/john-stones/25827" -o output/john-stones
```

Scrape one listing page:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/players" -o output/players
```

Scrape multiple consecutive listing pages:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/players?page=2&sort=rating&direction=desc" -o output/players --pages 5
```

That command scrapes pages `2` through `6`. To scrape the next five pages, start at page `7`:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/players?page=7&sort=rating&direction=desc" -o output/players --pages 5
```

Save image URLs without downloading images:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/players" -o output/players --no-download
```

## Output

Single-player scrape:

```text
output/john-stones/
  player.json
  card.html
  card_render.html
  images/
    background-1.webp
    player_image-1.webp
```

Listing-page scrape:

```text
output/players/
  index.json
  john-stones/
    91-cb-item-25-face-50535222-25827/
      player.json
      card.html
      card_render.html
      images/
```

The listing output groups cards by player name. Different versions of the same player are stored in nested variant directories so they do not overwrite each other.

`player.json` includes:

- source URL
- player/card name
- rating
- position
- visible face stats
- detailed profile stats, when scraping an individual profile page
- cleaned `.fc-card` HTML
- image URLs and local filenames

`card.html` contains only the cleaned card fragment.

`card_render.html` wraps the card in a minimal HTML page and links the FUTWIZ stylesheets so the card can be previewed more like it appears on FUTWIZ.

The exported card HTML removes these FUTWIZ side overlays:

- playstyles
- alternate positions
- skill moves, weak foot, and foot panel

## Cloudflare Handling

The script first tries a normal HTTP request. If FUTWIZ blocks that request, it opens a visible Chromium browser through Playwright.

The browser usually continues automatically once the real card content appears. If a Cloudflare or interstitial page remains visible, the script prompts you to complete the check manually and press Enter.

This does not bypass Cloudflare. It only lets the person running the script complete any required browser check normally.

## Saved HTML Fallback

If browser automation is unavailable, save the FUTWIZ page HTML from your browser and run:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/player/john-stones/25827" --html saved-page.html -o output/john-stones
```

`--html` is intended for one page at a time.

## Disclaimers

This project is for personal/local data collection and experimentation.

Before scraping FUTWIZ or using downloaded assets, review FUTWIZ's terms of service and robots policy. Player images, card designs, badges, flags, league logos, and other assets may be copyrighted or otherwise restricted.

Do not use this scraper to overload FUTWIZ. Avoid high request rates, repeated unnecessary downloads, or public redistribution of scraped assets unless you have the rights to do so.

The page structure on FUTWIZ can change. If class names like `.fc-card` or `.fc25-card__face__altimg` change, the scraper may need updates.
