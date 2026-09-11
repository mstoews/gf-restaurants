# Gluten-Free Tokyo Restaurants

A reusable Python script that searches the Japanese-language web for restaurants
in Tokyo's 23 special wards (特別区) that offer gluten-free menu items, then
records each one — name, address, ward, and the specific gluten-free dishes —
into a CSV "database".

It works by driving **Claude's built-in web search tool**: Claude runs the
Japanese search queries (グルテンフリー / グルテンフリー対応), reads the result
pages, and extracts structured data. The script writes and de-duplicates the CSV.

## Setup

```bash
cd ~/Desktop/gf-restaurants
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                  # your Anthropic API key
```

## Usage

```bash
# One ward (Japanese or English name both work)
python3 gf_restaurants.py --ward 渋谷区
python3 gf_restaurants.py --ward shibuya

# All 23 wards, up to 8 restaurants each
python3 gf_restaurants.py --all --max 8

# Custom output location
python3 gf_restaurants.py --ward 新宿区 --csv ~/Desktop/gf.csv
```

Options:

| Flag             | Default                        | Meaning                                   |
|------------------|--------------------------------|-------------------------------------------|
| `--ward`         | —                              | One ward (e.g. `渋谷区` or `shibuya`)      |
| `--all`          | —                              | Search all 23 special wards               |
| `--csv`          | `gluten_free_restaurants.csv`  | Output CSV path                           |
| `--max`          | `8`                            | Max restaurants per ward                  |
| `--max-searches` | `8`                            | Max web searches Claude runs per ward     |
| `--model`        | `claude-opus-4-8`              | Claude model                              |
| `--sleep`        | `2.0`                          | Pause between wards (for `--all`)         |

## The database

A CSV with one row per restaurant:

`name, name_en, ward, address, gluten_free_items, phone, source_url, image_url, notes, date_added`

- **Re-running is safe** — rows are de-duplicated by name + address, so running
  the same ward again only adds genuinely new restaurants.
- `gluten_free_items` is a `;`-separated list of the actual gluten-free dishes.
- `image_url` is captured but images are **not downloaded yet** (a placeholder
  for the planned "copy a picture" feature — the column is there so adding
  downloads later won't reshape the file).

## Notes & caveats

- Web-sourced data can be out of date or wrong. `notes` flags whether a place is
  fully gluten-free / celiac-safe vs. "some GF options," but **always confirm
  with the restaurant** before relying on it for dietary needs.
- The script asks Claude not to fabricate data, but verify anything critical.

## Planned next step

Add image downloading: fetch each `image_url` into an `images/` folder and store
the local path. The CSV already has the `image_url` column to support this.
