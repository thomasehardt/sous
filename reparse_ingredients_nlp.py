#!/usr/bin/env python3
"""
One-time re-parse of every recipe_ingredients row with the new
ingredient-parser-nlp-backed parse_ingredient() (see recipe_scaling.py),
replacing the output of the old regex heuristic. Only touches
recipe_ingredients.{quantity,unit,name,confidence,preparation} - raw_text
and the recipes table are untouched, so this can't desync anything else.

Also used to backfill the confidence column added for completeness
scoring (compute_completeness_scores.py), and the preparation column
(the ML parser's own labeled preparation span, e.g. "diced", split out
from name rather than folded into it) - rows written before either
column existed have confidence=NULL/preparation=NULL until this re-runs.

Idempotent (a deterministic re-parse of the same raw_text), safe to
re-run if interrupted. Commits every 2000 rows so a Ctrl-C only loses the
current batch, not the whole run.

Usage: python3 reparse_ingredients_nlp.py
"""
import sqlite3
import time

from recipe_model import RecipeDatabase
from recipe_scaling import parse_ingredient

BATCH_SIZE = 2000


def main():
    # Ensure any pending schema migrations (e.g. the preparation column)
    # have run - this script connects directly with sqlite3 rather than
    # through RecipeDatabase, so it wouldn't otherwise trigger them.
    RecipeDatabase('recipes.db')

    conn = sqlite3.connect('recipes.db')
    rows = conn.execute('SELECT id, raw_text FROM recipe_ingredients ORDER BY id').fetchall()
    total = len(rows)
    print(f"re-parsing {total} recipe_ingredients rows...")

    start = time.time()
    changed = 0
    for i, (row_id, raw_text) in enumerate(rows, 1):
        parsed = parse_ingredient(raw_text)
        conn.execute(
            'UPDATE recipe_ingredients SET quantity=?, unit=?, name=?, confidence=?, preparation=? WHERE id=?',
            (parsed['quantity'], parsed['unit'], parsed['name'], parsed['confidence'], parsed['preparation'], row_id),
        )
        changed += 1
        if i % BATCH_SIZE == 0:
            conn.commit()
            elapsed = time.time() - start
            rate = i / elapsed
            eta = (total - i) / rate
            print(f"  {i}/{total} ({elapsed:.0f}s elapsed, {eta:.0f}s remaining)")

    conn.commit()
    conn.close()
    print(f"done: {changed} rows re-parsed in {time.time()-start:.0f}s")


if __name__ == '__main__':
    main()
