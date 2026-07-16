#!/usr/bin/env python3
"""
Backfill missing ingredient quantities for the datahiveai batch (CC-BY-NC-4.0,
39,269 bare-ingredient recipes) using a reviewed match plan built against the
wilmerarltstrmberg/recipe-dataset-over-2m Kaggle dataset (RecipeNLG lineage).

The plan itself (title matching, NER-overlap scoring, cross-duplicate
majority voting, and an explicit filter for a known "missing slash" fraction
corruption pattern in some source rows) was built and reviewed separately -
see build_backfill_plan.py in the scratch working directory. This script
only applies that already-reviewed plan: prepend each recovered
quantity+unit prefix onto the existing ingredient text, then go through
RecipeDatabase.save_recipe() so recipe_ingredients/FTS stay in sync via the
app's own parser, same as the AkashPS11 backfill.

Usage: python3 backfill_recipenlg_quantities.py [--dry-run]
"""
import json
import sys

from recipe_model import RecipeDatabase

PLAN_PATH = '/tmp/claude-1000/-home-thomas-code-openclaw-in-docker/acac090e-0400-4454-bae1-3f7c5d8aa3a2/scratchpad/kaggle-recipenlg/backfill_plan.json'


def main():
    dry_run = '--dry-run' in sys.argv

    with open(PLAN_PATH) as f:
        plan = json.load(f)

    db = RecipeDatabase('recipes.db')

    updated = 0
    lines_filled = 0
    skipped_stale = 0

    for entry in plan:
        recipe = db.get_recipe(entry['id'])
        if recipe is None:
            skipped_stale += 1
            continue
        if recipe.ingredients != entry['old_ingredients']:
            # Recipe changed since the plan was built (e.g. by the earlier
            # AkashPS11 run, or manual edits) - don't blindly overwrite.
            skipped_stale += 1
            continue

        updated += 1
        lines_filled += entry['lines_filled']
        if dry_run:
            continue

        recipe.ingredients = entry['new_ingredients']
        db.save_recipe(recipe)  # updated_at untouched - preserves original

    print(f"{'[DRY RUN] ' if dry_run else ''}recipes updated: {updated}")
    print(f"ingredient lines filled: {lines_filled}")
    print(f"skipped, ingredients changed since plan was built: {skipped_stale}")


if __name__ == '__main__':
    main()
