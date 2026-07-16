#!/usr/bin/env python3
"""
Backfill missing cooking instructions for the datahiveai batch (39,447
recipes with license=CC-BY-NC-4.0 and no instructions - that source is
documented as "ingredients + nutrition only, not full step-by-step
recipes", per SPEC.md) using a reviewed match plan built against
wilmerarltstrmberg/recipe-dataset-over-2m (RecipeNLG lineage).

The plan (title matching against recipe_ingredients.name - not raw
recipes.ingredients text, which would still carry quantity prefixes from
the earlier RecipeNLG quantity backfill and break name matching -
ingredient-overlap scoring at the same 0.7 threshold used for the earlier
quantity backfill, and a junk-directions filter) was built and reviewed
separately - see build_instructions_plan.py in the scratch working
directory. This script only applies that already-reviewed plan.

License handling: RecipeNLG (this Kaggle mirror) is CC-BY-NC-SA-4.0
(ShareAlike), stricter than datahiveai's plain CC-BY-NC-4.0. Recipes
backfilled here now contain RecipeNLG-derived instructions text, so their
license is bumped to reflect both sources rather than left showing only
the less-restrictive original.

Usage: python3 backfill_recipenlg_instructions.py [--dry-run]
"""
import json
import sqlite3
import sys

from recipe_model import RecipeDatabase

PLAN_PATH = '/tmp/claude-1000/-home-thomas-code-openclaw-in-docker/acac090e-0400-4454-bae1-3f7c5d8aa3a2/scratchpad/kaggle-recipenlg/instr_plan.json'
COMPOUND_LICENSE = 'CC-BY-NC-4.0+CC-BY-NC-SA-4.0'


def main():
    dry_run = '--dry-run' in sys.argv

    with open(PLAN_PATH) as f:
        plan = json.load(f)

    db = RecipeDatabase('recipes.db')
    conn = sqlite3.connect('recipes.db')

    updated = 0
    skipped_stale = 0

    for entry in plan:
        row = conn.execute(
            'SELECT instructions, license FROM recipes WHERE id=?', (entry['id'],)
        ).fetchone()
        if row is None:
            skipped_stale += 1
            continue
        instructions_json, license = row
        current = json.loads(instructions_json) if instructions_json else []
        if current:
            # Instructions changed since the plan was built - don't overwrite.
            skipped_stale += 1
            continue

        updated += 1
        if dry_run:
            continue

        recipe = db.get_recipe(entry['id'])
        recipe.instructions = entry['directions']
        recipe.license = COMPOUND_LICENSE
        db.save_recipe(recipe)

    conn.close()
    print(f"{'[DRY RUN] ' if dry_run else ''}recipes updated: {updated}")
    print(f"skipped, instructions already present since plan was built: {skipped_stale}")


if __name__ == '__main__':
    main()
