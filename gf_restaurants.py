#!/usr/bin/env python3
"""
gf_restaurants.py — find gluten-free restaurants in Tokyo's 23 special wards.

Drives Claude's built-in web search tool to search the web *in Japanese*
(グルテンフリー / グルテンフリー対応) for restaurants in a given ward, extracts
the name, address, and which menu items are gluten free, and appends the
results to a CSV "database" (deduped by name + address).

Usage
-----
    export ANTHROPIC_API_KEY=sk-ant-...

    # One ward
    python3 gf_restaurants.py --ward 渋谷区

    # By English name (mapped to the Japanese ward)
    python3 gf_restaurants.py --ward shibuya

    # All 23 wards, up to 8 restaurants each
    python3 gf_restaurants.py --all --max 8

    # Custom output file
    python3 gf_restaurants.py --ward 新宿区 --csv ~/Desktop/gf.csv

The CSV is created on first run and appended to on subsequent runs. Re-running
the same ward will not create duplicate rows.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date


def _require_anthropic():
    """Import the SDK lazily so --help and offline tests work without it."""
    try:
        import anthropic
        return anthropic
    except ImportError:
        sys.exit(
            "The 'anthropic' package is required.\n"
            "Install it with:  pip3 install anthropic"
        )

# --- Tokyo's 23 special wards (特別区) -------------------------------------

WARDS = [
    "千代田区", "中央区", "港区", "新宿区", "文京区", "台東区",
    "墨田区", "江東区", "品川区", "目黒区", "大田区", "世田谷区",
    "渋谷区", "中野区", "杉並区", "豊島区", "北区", "荒川区",
    "板橋区", "練馬区", "足立区", "葛飾区", "江戸川区",
]

# English -> Japanese ward, so you can type --ward shibuya
ENGLISH_WARDS = {
    "chiyoda": "千代田区", "chuo": "中央区", "minato": "港区",
    "shinjuku": "新宿区", "bunkyo": "文京区", "taito": "台東区",
    "sumida": "墨田区", "koto": "江東区", "shinagawa": "品川区",
    "meguro": "目黒区", "ota": "大田区", "setagaya": "世田谷区",
    "shibuya": "渋谷区", "nakano": "中野区", "suginami": "杉並区",
    "toshima": "豊島区", "kita": "北区", "arakawa": "荒川区",
    "itabashi": "板橋区", "nerima": "練馬区", "adachi": "足立区",
    "katsushika": "葛飾区", "edogawa": "江戸川区",
}

CSV_FIELDS = [
    "name",                 # restaurant name (Japanese)
    "name_en",              # romanized / English name, if known
    "ward",                 # 23-ward name (Japanese)
    "address",              # full address
    "gluten_free_items",    # ; separated list of GF menu items
    "phone",                # phone number, if found
    "source_url",           # where the info came from
    "image_url",            # photo URL (downloading handled later)
    "notes",                # dedicated GF restaurant? cross-contamination notes, etc.
    "date_added",           # YYYY-MM-DD this row was recorded
]

MODEL_DEFAULT = "claude-opus-4-8"


def build_prompt(ward, max_results):
    """Instruction (in Japanese + English) telling Claude what to find."""
    return (
        f"あなたはグルテンフリー対応レストランのリサーチャーです。"
        f"東京都{ward}にある、グルテンフリー（gluten free）のメニューを"
        f"提供しているレストランやカフェを、日本語でウェブ検索して探してください。\n\n"
        f"「{ward} グルテンフリー」「{ward} グルテンフリー レストラン」"
        f"「{ward} グルテンフリー 対応」などのクエリで複数回検索し、"
        f"信頼できる情報源（公式サイト、食べログ、専門ブログ等）を確認してください。\n\n"
        f"最大{max_results}件まで、実在が確認できたお店だけを挙げてください。\n\n"
        "For each restaurant, record: the name (Japanese, and English/romaji if "
        "available), the full address, the SPECIFIC menu items that are gluten "
        "free (not just 'has GF options' — list the actual dishes), phone number "
        "if shown, the source URL you used, and a representative photo URL if one "
        "is on the page. In notes, say whether it's a fully gluten-free/celiac-"
        "safe restaurant or only offers some GF items, plus any cross-"
        "contamination caveats.\n\n"
        "After you finish researching, respond with ONLY a JSON array (no prose, "
        "no markdown fences) of objects with exactly these keys: "
        '"name", "name_en", "ward", "address", "gluten_free_items" (an array of '
        'strings), "phone", "source_url", "image_url", "notes". '
        "Use an empty string for anything you could not find. Do not invent data."
    )


def extract_json_array(text):
    """Pull the JSON array out of Claude's final text response."""
    # Strip ```json fences if present
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        # Fall back to the first bracket-balanced array in the text
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
    if candidate is None:
        return []
    try:
        data = json.loads(candidate)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def search_ward(client, ward, max_results, model, max_searches):
    """Run one ward through Claude + web search; return list of dict rows."""
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=(
            "You research restaurants by searching the Japanese-language web. "
            "Be accurate and only report places you can verify. Never fabricate "
            "addresses or menu items."
        ),
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_searches,
        }],
        messages=[{"role": "user", "content": build_prompt(ward, max_results)}],
    )

    # Concatenate the model's final text blocks (skip tool-result blocks)
    text = "".join(
        block.text for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    records = extract_json_array(text)

    rows = []
    today = date.today().isoformat()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        items = rec.get("gluten_free_items", "")
        if isinstance(items, list):
            items = "; ".join(str(i).strip() for i in items if str(i).strip())
        rows.append({
            "name": str(rec.get("name", "")).strip(),
            "name_en": str(rec.get("name_en", "")).strip(),
            "ward": str(rec.get("ward", "")).strip() or ward,
            "address": str(rec.get("address", "")).strip(),
            "gluten_free_items": items,
            "phone": str(rec.get("phone", "")).strip(),
            "source_url": str(rec.get("source_url", "")).strip(),
            "image_url": str(rec.get("image_url", "")).strip(),
            "notes": str(rec.get("notes", "")).strip(),
            "date_added": today,
        })
    return [r for r in rows if r["name"]]


def dedup_key(row):
    """Normalize name+address so re-runs don't duplicate a restaurant."""
    norm = lambda s: re.sub(r"\s+", "", (s or "")).lower()
    return (norm(row["name"]), norm(row["address"]))


def load_existing_keys(csv_path):
    keys = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add(dedup_key(row))
    return keys


def append_rows(csv_path, rows):
    """Append rows to the CSV, creating it with a header if needed."""
    existing = load_existing_keys(csv_path)
    new_rows = []
    for r in rows:
        k = dedup_key(r)
        if k in existing:
            continue
        existing.add(k)
        new_rows.append(r)

    write_header = not os.path.exists(csv_path)
    if new_rows:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
    return len(new_rows), len(rows) - len(new_rows)


def resolve_ward(value):
    v = value.strip()
    if v in WARDS:
        return v
    low = v.lower().replace("-ku", "").replace(" ", "")
    if low in ENGLISH_WARDS:
        return ENGLISH_WARDS[low]
    raise SystemExit(
        f"Unknown ward: {value!r}. Use a Japanese ward like 渋谷区, "
        f"or an English name like 'shibuya'."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Find gluten-free restaurants in Tokyo's 23 wards and "
                    "record them in a CSV database."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ward", help="A single ward (渋谷区 or 'shibuya')")
    group.add_argument("--all", action="store_true",
                       help="Search all 23 special wards")
    parser.add_argument("--csv", default="gluten_free_restaurants.csv",
                        help="Output CSV path (default: ./gluten_free_restaurants.csv)")
    parser.add_argument("--max", type=int, default=8,
                        help="Max restaurants to find per ward (default: 8)")
    parser.add_argument("--max-searches", type=int, default=8,
                        help="Max web searches Claude may run per ward (default: 8)")
    parser.add_argument("--model", default=MODEL_DEFAULT,
                        help=f"Claude model (default: {MODEL_DEFAULT})")
    parser.add_argument("--sleep", type=float, default=2.0,
                        help="Seconds to pause between wards (default: 2.0)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first:  export ANTHROPIC_API_KEY=sk-ant-...")

    anthropic = _require_anthropic()
    client = anthropic.Anthropic()
    targets = WARDS if args.all else [resolve_ward(args.ward)]
    csv_path = os.path.expanduser(args.csv)

    grand_new = grand_dup = 0
    for i, ward in enumerate(targets):
        print(f"[{i+1}/{len(targets)}] Searching {ward} …", flush=True)
        try:
            rows = search_ward(client, ward, args.max, args.model, args.max_searches)
        except anthropic.APIError as e:
            print(f"  ! API error for {ward}: {e}", file=sys.stderr)
            continue
        added, dup = append_rows(csv_path, rows)
        grand_new += added
        grand_dup += dup
        print(f"  found {len(rows)} | added {added} new | {dup} already known",
              flush=True)
        if args.all and i < len(targets) - 1:
            time.sleep(args.sleep)

    print(f"\nDone. {grand_new} new restaurant(s) added, "
          f"{grand_dup} duplicate(s) skipped.")
    print(f"Database: {csv_path}")


if __name__ == "__main__":
    main()
