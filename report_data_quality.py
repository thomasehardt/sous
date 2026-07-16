#!/usr/bin/env python3
"""
Report on recipe data quality using recipes.completeness_score (see
compute_completeness_scores.py). Read-only - for finding recipes worth
manual cleanup or a targeted backfill, not for the live app.

Usage:
  python3 report_data_quality.py                  # summary + worst 20
  python3 report_data_quality.py --worst N         # worst N recipes
  python3 report_data_quality.py --below SCORE     # all recipes under SCORE
  python3 report_data_quality.py --recipe ID        # full breakdown for one recipe
"""
import json
import sqlite3
import sys

from compute_completeness_scores import score_recipe, WEIGHTS, _JUNK_INGREDIENT_NAMES


def summary(conn):
    rows = conn.execute('SELECT completeness_score FROM recipes WHERE completeness_score IS NOT NULL').fetchall()
    values = sorted(r[0] for r in rows)
    n = len(values)
    print(f"{n} recipes scored")
    print(f"  min {values[0]}  p10 {values[n//10]}  median {values[n//2]}  p90 {values[int(n*0.9)]}  max {values[-1]}")
    buckets = [(0, 25), (25, 50), (50, 75), (75, 100.001)]
    for lo, hi in buckets:
        count = sum(1 for v in values if lo <= v < hi)
        print(f"  [{lo:>3.0f}-{hi:>3.0f}): {count} ({count/n*100:.1f}%)")


def worst(conn, n):
    rows = conn.execute(
        'SELECT id, title, completeness_score FROM recipes ORDER BY completeness_score ASC LIMIT ?', (n,)
    ).fetchall()
    for rid, title, score in rows:
        print(f"  [{rid}] {score:>5.1f}  {title}")


def below(conn, threshold):
    rows = conn.execute(
        'SELECT id, title, completeness_score FROM recipes WHERE completeness_score < ? ORDER BY completeness_score ASC',
        (threshold,),
    ).fetchall()
    print(f"{len(rows)} recipes below {threshold}")
    for rid, title, score in rows[:50]:
        print(f"  [{rid}] {score:>5.1f}  {title}")
    if len(rows) > 50:
        print(f"  ... and {len(rows) - 50} more")


def explain(conn, recipe_id):
    row = conn.execute(
        'SELECT title, instructions, image_url, nutrition, completeness_score FROM recipes WHERE id=?', (recipe_id,)
    ).fetchone()
    if row is None:
        print(f"no recipe with id {recipe_id}")
        return
    title, instructions_json, image_url, nutrition, stored_score = row
    ingredient_rows = conn.execute(
        'SELECT quantity, unit, name, confidence FROM recipe_ingredients WHERE recipe_id=? ORDER BY position', (recipe_id,)
    ).fetchall()

    print(f"[{recipe_id}] {title!r}")
    print(f"stored completeness_score: {stored_score}")
    print()

    instructions = json.loads(instructions_json) if instructions_json else []
    n = len(ingredient_rows)
    names = {(r[2] or '').strip().lower() for r in ingredient_rows}
    junk_found = names & _JUNK_INGREDIENT_NAMES
    confidences = [r[3] for r in ingredient_rows if r[3] is not None]

    print(f"has_instructions: {bool(instructions)}  (weight {WEIGHTS['has_instructions']})")
    print(f"ingredient count: {n}  (sane={n>=2}, weight {WEIGHTS['ingredient_count_sane']})")
    if n:
        qty_cov = sum(1 for r in ingredient_rows if r[0] is not None) / n
        print(f"quantity_coverage: {qty_cov:.2f}  (weight {WEIGHTS['quantity_coverage']})")
    print(f"junk ingredient names found: {junk_found or 'none'}  (weight {WEIGHTS['no_junk_ingredients']})")
    if confidences:
        print(f"avg_parse_confidence: {sum(confidences)/len(confidences):.3f} over {len(confidences)}/{n} lines  (weight {WEIGHTS['avg_parse_confidence']})")
    print(f"has_image: {bool(image_url)}  (weight {WEIGHTS['has_image']})")
    print(f"has_nutrition: {bool(nutrition)}  (weight {WEIGHTS['has_nutrition']})")
    print()
    print("recomputed score:", score_recipe(instructions_json, image_url, nutrition, ingredient_rows))


def main():
    conn = sqlite3.connect('recipes.db')
    args = sys.argv[1:]

    if '--recipe' in args:
        explain(conn, int(args[args.index('--recipe') + 1]))
    elif '--worst' in args:
        worst(conn, int(args[args.index('--worst') + 1]))
    elif '--below' in args:
        below(conn, float(args[args.index('--below') + 1]))
    else:
        summary(conn)
        print()
        print("worst 20:")
        worst(conn, 20)

    conn.close()


if __name__ == '__main__':
    main()
