#!/usr/bin/env python3
"""
Fix a pre-existing data corruption in the Hieu-Pham batch (recipe ids
starting at 2449): 1,890 recipes have `ingredients` stored as a
single-element JSON array whose one element is itself a Python
list-literal string (e.g. '["[\'¾ cup dal\', \'¼ cup rice\', ...]"]')
instead of a proper JSON array of ingredient strings. Root import script
not identified (not import_real_data.py's clean_ingredients(), which is
built for AkashPS11's different R-style c("a","b") format and wouldn't
produce this shape - likely a one-off Hieu-Pham-specific import path not
kept in the repo).

Recovers the real ingredient list via ast.literal_eval (verified safe:
all 1,890 rows parse as a list of strings, none raise, none produce a
non-list/non-string shape) and saves through RecipeDatabase.save_recipe(),
so recipe_ingredients/FTS re-sync automatically via the current parser.

Usage: python3 fix_double_stringified_ingredients.py [--dry-run]
"""
import ast
import json
import sqlite3
import sys

from recipe_model import RecipeDatabase


def find_corrupted(conn):
    rows = conn.execute('SELECT id, ingredients FROM recipes').fetchall()
    corrupted = []
    for rid, ing_json in rows:
        try:
            ing = json.loads(ing_json) if ing_json else []
        except (json.JSONDecodeError, TypeError):
            continue
        if len(ing) == 1 and ing[0].strip().startswith('[') and "', '" in ing[0]:
            corrupted.append((rid, ing[0]))
    return corrupted


def main():
    dry_run = '--dry-run' in sys.argv

    conn = sqlite3.connect('recipes.db')
    corrupted = find_corrupted(conn)
    conn.close()
    print(f"found {len(corrupted)} corrupted recipes")

    db = RecipeDatabase('recipes.db')
    fixed = 0
    skipped = 0
    for rid, raw in corrupted:
        try:
            recovered = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            skipped += 1
            continue
        if not (isinstance(recovered, list) and all(isinstance(x, str) for x in recovered)):
            skipped += 1
            continue

        if dry_run:
            fixed += 1
            continue

        recipe = db.get_recipe(rid)
        updated_at = recipe.updated_at
        recipe.ingredients = recovered
        recipe.updated_at = updated_at  # preserve original timestamp
        db.save_recipe(recipe)
        fixed += 1

    print(f"{'[DRY RUN] ' if dry_run else ''}recipes fixed: {fixed}")
    print(f"skipped (recovery failed or unexpected shape): {skipped}")


if __name__ == '__main__':
    main()
