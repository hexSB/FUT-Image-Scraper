# FUTWIZ Image Scraper

Scrapes a FUTWIZ FC player page, saves `player.json`, and downloads discovered image assets into `images/`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Run

Single player:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/player/john-stones/25827" -o output/john-stones
```

Players listing page:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/players" -o output/players
```

Multiple consecutive listing pages:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/players?page=2&sort=rating&direction=desc" -o output/players --pages 5
```

The script tries a normal HTTP request first. If FUTWIZ returns Cloudflare, it opens a visible browser. Complete the Cloudflare check in that browser, wait until the FUTWIZ player page is visible, then press Enter in the terminal.

## Output

`player.json` contains:

- player name, version, rating, and position
- profile fields such as club, league, nationality, skill moves, weak foot, height, player ID, and card ID
- face stats and detailed in-game stats
- the full `.fc-card` HTML in the `card_html` JSON field, also written to `card.html`
- a standalone `card_render.html` file that includes the page CSS links for visual rendering
- the card background image and player image URLs, with local filenames for downloaded files

Images are limited to the card background and player image used by the card HTML.

The exported card HTML removes FUTWIZ's side overlays for playstyles, alternate positions, and skill/weak-foot/foot details.

For listing pages, each profile is saved under a player-name folder with a nested card-variant folder. This keeps cards for players with the same name together without overwriting distinct images or card types. The top-level `index.json` contains the profile URLs, output folders, names, ratings, positions, and any failures.

Listing-page scraping uses the rendered cards already present on the listing page. It does not open every player profile URL. Because of that, listing outputs include the card HTML, render page, card images, and visible face stats from the card; detailed profile-only fields remain empty unless you scrape an individual profile URL.

## Saved HTML Fallback

If the browser flow is not available, save the FUTWIZ page HTML from your browser and run:

```bash
python scrape_futwiz.py "https://www.futwiz.com/fc26/player/john-stones/25827" --html saved-page.html -o output/john-stones
```

Use `--no-download` to store only image URLs.
