#!/usr/bin/env python3
"""
Backfill missing ingredient quantities for the AkashPS11 batch (recipes
1226-2448). import_real_data.py originally only read RecipeIngredientParts
from data/recipes.parquet and never touched the adjacent
RecipeIngredientQuantities column, so ~1,221 recipes ended up with bare
ingredient names ("saffron", "milk") instead of quantity-prefixed text
("1 saffron", "4 milk"). The quantities were sitting in the source file
the whole time.

Matches db recipes to parquet rows by title, then verifies the parquet's
RecipeIngredientParts list is positionally identical to the db's stored
ingredients before touching anything - skips (and logs) anything that
doesn't match exactly rather than guessing. Reuses RecipeDatabase.save_recipe
so recipe_ingredients/FTS stay in sync via the app's own parser instead of
writing a parallel path.

Usage: python3 backfill_akashps11_quantities.py [--dry-run]
"""
import json
import re
import sys

import pyarrow.parquet as pq

from recipe_model import RecipeDatabase

BATCH_ID_LOW, BATCH_ID_HIGH = 1226, 2448


def parse_vec(s):
    if not s:
        return []
    return [m.group(1) for m in re.finditer(r'"([^"]*)"', s)]


def parse_qty_vec(s):
    if not s:
        return []
    return [m.group(1) for m in re.finditer(r'"([^"]*)"|NA', s)]


def load_parquet_index():
    t = pq.read_table(
        'data/recipes.parquet',
        columns=['Name', 'RecipeIngredientParts', 'RecipeIngredientQuantities'],
    )
    names = t.column('Name').to_pylist()
    parts = t.column('RecipeIngredientParts').to_pylist()
    qtys = t.column('RecipeIngredientQuantities').to_pylist()

    by_title = {}
    for i, p in enumerate(parts):
        if not p:
            continue
        by_title.setdefault(names[i], []).append(i)

    return names, parts, qtys, by_title


def main():
    dry_run = '--dry-run' in sys.argv

    names, parts, qtys, by_title = load_parquet_index()
    db = RecipeDatabase('recipes.db')

    import sqlite3
    conn = sqlite3.connect('recipes.db')
    db_rows = conn.execute(
        'SELECT id, title, ingredients, updated_at FROM recipes WHERE license="MIT" AND id BETWEEN ? AND ?',
        (BATCH_ID_LOW, BATCH_ID_HIGH),
    ).fetchall()
    conn.close()

    updated = 0
    lines_filled = 0
    lines_left_bare = 0
    skipped_ambiguous_title = 0
    skipped_shape_mismatch = 0

    for rid, title, ing_json, updated_at in db_rows:
        ing = json.loads(ing_json) if ing_json else []
        cands = by_title.get(title, [])
        if len(cands) != 1:
            skipped_ambiguous_title += 1
            continue
        i = cands[0]
        p_parts = parse_vec(parts[i])
        if p_parts != ing:
            skipped_shape_mismatch += 1
            continue

        p_qtys = parse_qty_vec(qtys[i])
        if len(p_qtys) != len(ing):
            skipped_shape_mismatch += 1
            continue

        new_ing = []
        changed = False
        for name, qty_token in zip(ing, p_qtys):
            if qty_token is None:
                new_ing.append(name)
                lines_left_bare += 1
            else:
                new_ing.append(f"{qty_token} {name}")
                lines_filled += 1
                changed = True
        if not changed:
            continue

        updated += 1
        if dry_run:
            if updated <= 5:
                print(f"[{rid}] {title}")
                for old, new in zip(ing[:4], new_ing[:4]):
                    print(f"    {old!r} -> {new!r}")
            continue

        recipe = db.get_recipe(rid)
        recipe.ingredients = new_ing
        recipe.updated_at = updated_at  # preserve original timestamp
        db.save_recipe(recipe)

    print()
    print(f"{'[DRY RUN] ' if dry_run else ''}recipes updated: {updated}")
    print(f"ingredient lines filled with quantity: {lines_filled}")
    print(f"ingredient lines left bare (no quantity in source, e.g. 'salt'): {lines_left_bare}")
    print(f"skipped, ambiguous duplicate title: {skipped_ambiguous_title}")
    print(f"skipped, shape mismatch: {skipped_shape_mismatch}")


if __name__ == '__main__':
    main()
