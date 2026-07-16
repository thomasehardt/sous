#!/usr/bin/env python3
"""
One-time import of RecipeNLG Lite (m3hrdadfi/recipe_nlg_lite, MIT licensed,
7,198 recipes) - a fourth source batch, evaluated 2026-07-14 as a
higher-quality alternative to the existing CC-BY-NC-4.0 batch (96%+ of its
ingredient lines carry a real quantity, vs. ~39% corpus-wide today; every
row has non-empty ingredients and steps).

Source file: recipe_nlg_lite/all_data.csv, tab-separated, columns
uid/name/description/link/ner/ingredients/steps - downloaded directly from
the dataset's public Google Drive link (no manual click-through gate, unlike
the parent RecipeNLG dataset). Not re-downloaded by this script; point
--csv at wherever it was extracted.

Format caveat (documented, not silently papered over): `ingredients` and
`steps` are comma-joined strings in this dataset, not proper lists, so the
original item/step boundaries are lossy - a prep note like "crisp cooked and
crumbled" can land as its own pseudo-ingredient rather than staying attached
to the item before it, and step boundaries are inferred from ". " rather
than a real delimiter. Deliberately NOT hand-rolling a smarter merge
heuristic here: each comma-split ingredient segment is passed through
unchanged as its own recipes.ingredients line, and left to this project's
existing recipe_scaling.parse_ingredient() (ingredient-parser-nlp, already
verified at 98.5% agreement on this project's real corpus) to parse -
consistent with how every other ingredient line in this app is handled,
rather than inventing new untested logic for this one source.

Skips any row whose title (case-insensitive, stripped) already exists in
recipes.title - 464 of 7,198 rows overlap the existing corpus by this
measure (checked 2026-07-14), so ~6,734 are net-new.

Retries once on sqlite3 lock contention (a separate long-running backfill,
reparse_ingredients_nlp.py, may be writing to the same recipes.db
concurrently) rather than crashing the whole import over a transient lock.

Usage: .venv/bin/python import_recipe_nlg_lite.py --csv <path/to/all_data.csv> [--dry-run]
"""
import argparse
import csv
import re
import sqlite3
import sys
import time

from recipe_model import Recipe, RecipeDatabase

STEP_SPLIT_RE = re.compile(r'\s*\.\s+')


def parse_ingredients(raw: str) -> list[str]:
    return [seg.strip() for seg in raw.split(',') if seg.strip()]


def parse_steps(raw: str) -> list[str]:
    parts = [p.strip() for p in STEP_SPLIT_RE.split(raw) if p.strip()]
    return parts


def save_with_retry(db: RecipeDatabase, recipe: Recipe, max_retries: int = 3) -> int:
    for attempt in range(max_retries):
        try:
            return db.save_recipe(recipe)
        except sqlite3.OperationalError as e:
            if 'locked' not in str(e).lower() or attempt == max_retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError('unreachable')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', required=True, help='Path to recipe_nlg_lite/all_data.csv')
    ap.add_argument('--dry-run', action='store_true', help='Parse and report counts, write nothing')
    args = ap.parse_args()

    db = RecipeDatabase('recipes.db')
    existing_titles = {r[0].strip().lower() for r in
                        sqlite3.connect('recipes.db').execute('SELECT title FROM recipes')}

    with open(args.csv, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        rows = list(reader)

    print(f"read {len(rows)} rows from {args.csv}")

    imported = 0
    skipped_dupe = 0
    skipped_blank = 0
    start = time.time()

    for i, row in enumerate(rows, 1):
        title = (row.get('name') or '').strip()
        if not title:
            skipped_blank += 1
            continue
        if title.lower() in existing_titles:
            skipped_dupe += 1
            continue

        ingredients = parse_ingredients(row.get('ingredients') or '')
        instructions = parse_steps(row.get('steps') or '')
        if not ingredients or not instructions:
            skipped_blank += 1
            continue

        if not args.dry_run:
            recipe = Recipe(
                title=title,
                description=(row.get('description') or '').strip(),
                ingredients=ingredients,
                instructions=instructions,
                servings=1,
                cuisine='',
                url=(row.get('link') or '').strip(),
                license='MIT',
            )
            save_with_retry(db, recipe)
            existing_titles.add(title.lower())  # guard against dupes within this same source file

        imported += 1
        if i % 500 == 0:
            elapsed = time.time() - start
            print(f"  {i}/{len(rows)} processed, {imported} imported, {elapsed:.0f}s elapsed")

    print(f"done: {imported} imported, {skipped_dupe} skipped as duplicate titles, "
          f"{skipped_blank} skipped as blank/unparseable, in {time.time()-start:.0f}s"
          + (" (dry run, nothing written)" if args.dry_run else ""))


if __name__ == '__main__':
    main()
