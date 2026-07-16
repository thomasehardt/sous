#!/usr/bin/env python3
"""
Compute and persist a 0-100 data-quality "completeness score" for every
recipe, into recipes.completeness_score. Computed once and stored - not
recomputed per request - same pattern this project already uses for
recipe_ingredients/recipe_steps.

The score is a weighted blend of signals discovered/characterized during
this project's ingredient-quantity and instructions backfill work:

  has_instructions        0.30  binary - the single biggest usability gap
                                 found in this corpus (61% of recipes)
  ingredient_count_sane    0.20  binary - 0 if <=1 ingredients (~50 recipes
                                 are this broken)
  quantity_coverage        0.20  fraction of this recipe's ingredient lines
                                 with a parsed quantity
  no_junk_ingredients      0.10  binary - 0 if any ingredient name is a
                                 known parser/scrape artifact (see
                                 _JUNK_INGREDIENT_NAMES; not exhaustive,
                                 a heuristic like the rest of this
                                 project's text-parsing code)
  avg_parse_confidence     0.10  ingredient_parser_nlp's own average
                                 per-line confidence (see
                                 recipe_scaling.parse_ingredient) - the one
                                 signal here that isn't just recombining
                                 data already visible elsewhere in the app
  has_image                0.05  binary
  has_nutrition            0.05  binary

Weights sum to 1.0; final score is scaled to 0-100.

Usage: python3 compute_completeness_scores.py [--dry-run]
"""
import json
import sqlite3
import sys

# Ingredient names that are parser/scrape artifacts, not real food - found
# by hand while reviewing ingredient_totals during the co-occurrence rekey
# and flavor-tagging work this session (empty flavor-list entries that
# were genuinely non-food, not just flavorless real ingredients like
# "water" or "ice"). Not exhaustive - a documented heuristic, same as
# every other text-parsing heuristic in this project.
_JUNK_INGREDIENT_NAMES = {
    'divided', 'chopped', 'sliced', 'trimmed', 'peeled', 'pitted', 'optional',
    'garnish', 'roasted', 'cleaned', 'clean', 'crust', 'filling', 'dressing',
    'spices', 'spice', 'ingredients', 'marinade', 'sauce', 'green', 'white',
    'hours', 'roast', 'tart', 'dried', 'none', 'coarsely chopped',
    'finely chopped', 'thinly sliced', 'cut into strips', 'cut into quarters',
    'cut into bits', 'cut into thin strips', 'very coarsely chopped',
    'thinly sliced lengthwise', ')', '"',
}

WEIGHTS = {
    'has_instructions': 0.30,
    'ingredient_count_sane': 0.20,
    'quantity_coverage': 0.20,
    'no_junk_ingredients': 0.10,
    'avg_parse_confidence': 0.10,
    'has_image': 0.05,
    'has_nutrition': 0.05,
}


def score_recipe(instructions_json, image_url, nutrition, ingredient_rows):
    instructions = json.loads(instructions_json) if instructions_json else []
    has_instructions = 1.0 if instructions else 0.0

    n = len(ingredient_rows)
    ingredient_count_sane = 1.0 if n >= 2 else 0.0

    if n > 0:
        quantity_coverage = sum(1 for r in ingredient_rows if r[0] is not None) / n
        names = {(r[2] or '').strip().lower() for r in ingredient_rows}
        no_junk_ingredients = 0.0 if names & _JUNK_INGREDIENT_NAMES else 1.0
        confidences = [r[3] for r in ingredient_rows if r[3] is not None]
        avg_parse_confidence = sum(confidences) / len(confidences) if confidences else 0.7
    else:
        quantity_coverage = 0.0
        no_junk_ingredients = 1.0  # nothing to be junk
        avg_parse_confidence = 0.0

    has_image = 1.0 if image_url else 0.0
    has_nutrition = 1.0 if nutrition else 0.0

    components = {
        'has_instructions': has_instructions,
        'ingredient_count_sane': ingredient_count_sane,
        'quantity_coverage': quantity_coverage,
        'no_junk_ingredients': no_junk_ingredients,
        'avg_parse_confidence': avg_parse_confidence,
        'has_image': has_image,
        'has_nutrition': has_nutrition,
    }
    score = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    return round(score * 100, 1)


def main():
    dry_run = '--dry-run' in sys.argv

    conn = sqlite3.connect('recipes.db')
    recipes = conn.execute('SELECT id, instructions, image_url, nutrition FROM recipes').fetchall()

    ingredients_by_recipe = {}
    for recipe_id, quantity, unit, name, confidence in conn.execute(
        'SELECT recipe_id, quantity, unit, name, confidence FROM recipe_ingredients'
    ):
        ingredients_by_recipe.setdefault(recipe_id, []).append((quantity, unit, name, confidence))

    print(f"scoring {len(recipes)} recipes...")
    scores = []
    for recipe_id, instructions_json, image_url, nutrition in recipes:
        score = score_recipe(instructions_json, image_url, nutrition, ingredients_by_recipe.get(recipe_id, []))
        scores.append((score, recipe_id))

    if not dry_run:
        conn.executemany('UPDATE recipes SET completeness_score=? WHERE id=?', scores)
        conn.commit()
    conn.close()

    values = sorted(s for s, _ in scores)
    n = len(values)
    print(f"{'[DRY RUN] ' if dry_run else ''}scored {n} recipes")
    print(f"min {values[0]}, p10 {values[n//10]}, median {values[n//2]}, p90 {values[int(n*0.9)]}, max {values[-1]}")


if __name__ == '__main__':
    main()
