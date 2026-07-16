"""One-time backfill of image_url/nutrition for recipes whose source dataset
had that data but it was never captured during the original import.

Two sources, both already covered by this project's existing per-recipe
`license` tracking (see SPEC.md "Dataset Sources") - this backfill adds more
fields from datasets already vetted, it does not introduce a new license
question:

- datahiveai/recipes-with-nutrition (CC-BY-NC-4.0, 39,447 recipes already in
  recipes.db): matched by exact `url` - the same URL already stored
  verbatim on import, so this is an exact join key, not fuzzy matching.
- AkashPS11/recipes_data_food.com (MIT, 1,223 recipes already in recipes.db,
  cached locally at data/recipes.parquet): matched by exact `title`, since
  no original source id was preserved during the first import.

Deliberately NOT covered: Hieu-Pham/kaggle_food_recipes (MIT, 13,495
recipes) has no nutrition columns at all, and its `Image_Name` field is a
bare filename slug with no host/URL - there is no usable image or
nutrition data in that source. This is a real, permanent gap in the
underlying data, not a bug - left undone rather than fabricating a broken
image link or fake numbers.
"""
import json
import re
import sqlite3

import pyarrow.parquet as pq

# The subset of total_nutrients keys worth a compact human-readable summary.
# Full daily_values/digest blobs are excluded as noise beyond what a print
# view or recipe page needs.
NUTRIENT_LABELS = [
    ('ENERC_KCAL', 'Calories', 'kcal', 0),
    ('PROCNT', 'Protein', 'g', 1),
    ('FAT', 'Fat', 'g', 1),
    ('CHOCDF', 'Carbs', 'g', 1),
    ('FIBTG', 'Fiber', 'g', 1),
    ('SUGAR', 'Sugar', 'g', 1),
    ('NA', 'Sodium', 'mg', 0),
]


def _format_datahiveai_nutrition(total_nutrients_json: str) -> str:
    try:
        nutrients = json.loads(total_nutrients_json) if total_nutrients_json else {}
    except (json.JSONDecodeError, TypeError):
        return ''
    parts = []
    for key, label, unit, decimals in NUTRIENT_LABELS:
        entry = nutrients.get(key)
        if not entry or 'quantity' not in entry:
            continue
        value = round(entry['quantity'], decimals)
        if decimals == 0:
            value = int(value)
        parts.append(f'{label}: {value}{unit}')
    return ' | '.join(parts)


def backfill_from_datahiveai(parquet_path: str, db_path: str = 'recipes.db') -> dict:
    """Match by exact recipes.url. Returns {'matched': n, 'total_rows': n}."""
    table = pq.read_table(parquet_path)
    by_url = {}
    for row in table.to_pylist():
        url = row.get('url')
        if url:
            by_url[url] = row

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, url FROM recipes WHERE license = 'CC-BY-NC-4.0'")
    db_rows = cursor.fetchall()

    matched = 0
    for recipe_id, url in db_rows:
        source = by_url.get(url)
        if not source:
            continue
        image_url = source.get('image_url') or ''
        nutrition = _format_datahiveai_nutrition(source.get('total_nutrients'))
        cursor.execute(
            'UPDATE recipes SET image_url = ?, nutrition = ? WHERE id = ?',
            (image_url, nutrition, recipe_id),
        )
        matched += 1

    conn.commit()
    conn.close()
    return {'matched': matched, 'total_rows': len(db_rows)}


_R_VECTOR_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _parse_r_vector_first_string(value) -> str:
    """AkashPS11's Images column is an R-style vector string like
    c("url1", "url2"). Returns the first quoted string, or '' if the
    field isn't in that shape (some rows are NA/None)."""
    if not isinstance(value, str):
        return ''
    match = _R_VECTOR_STRING_RE.search(value)
    return match.group(1) if match else ''


_AKASH_NUTRIENT_COLUMNS = [
    ('Calories', 'Calories', 'kcal', 0),
    ('ProteinContent', 'Protein', 'g', 1),
    ('FatContent', 'Fat', 'g', 1),
    ('CarbohydrateContent', 'Carbs', 'g', 1),
    ('FiberContent', 'Fiber', 'g', 1),
    ('SugarContent', 'Sugar', 'g', 1),
    ('SodiumContent', 'Sodium', 'mg', 0),
]


def _format_akash_nutrition(row: dict) -> str:
    parts = []
    for col, label, unit, decimals in _AKASH_NUTRIENT_COLUMNS:
        value = row.get(col)
        if value is None:
            continue
        value = round(value, decimals)
        if decimals == 0:
            value = int(value)
        parts.append(f'{label}: {value}{unit}')
    return ' | '.join(parts)


AKASH_ID_RANGE = (1226, 2448)  # confirmed exact boundary: id 1226 = "Low-Fat Berry
# Blue Frozen Dessert" (AkashPS11's first usable row), id 2449 = "Miso-Butter Roast
# Chicken..." (Hieu-Pham's row 0) - both datasets share license='MIT' and empty url,
# so this contiguous id range (not license/url) is the only reliable way to isolate
# the 1,223 AkashPS11 rows from the 13,495 Hieu-Pham rows that were imported right
# after them with no gap in between.


def backfill_from_akash(parquet_path: str, db_path: str = 'recipes.db') -> dict:
    """Match by exact recipes.title against the AkashPS11 id range. Title
    collisions in the source (a handful of duplicate names in a
    million-row dataset) are skipped rather than guessed at - only titles
    unique in the source parquet are used as a match key."""
    table = pq.read_table(parquet_path)
    by_title = {}
    ambiguous = set()
    for row in table.to_pylist():
        name = row.get('Name')
        if not name:
            continue
        if name in by_title:
            ambiguous.add(name)
        else:
            by_title[name] = row
    for name in ambiguous:
        del by_title[name]

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, title FROM recipes WHERE license = 'MIT' AND id BETWEEN ? AND ?",
        AKASH_ID_RANGE,
    )
    db_rows = cursor.fetchall()

    matched = 0
    for recipe_id, title in db_rows:
        source = by_title.get(title)
        if not source:
            continue
        image_url = _parse_r_vector_first_string(source.get('Images'))
        nutrition = _format_akash_nutrition(source)
        cursor.execute(
            'UPDATE recipes SET image_url = ?, nutrition = ? WHERE id = ?',
            (image_url, nutrition, recipe_id),
        )
        matched += 1

    conn.commit()
    conn.close()
    return {'matched': matched, 'total_rows': len(db_rows), 'ambiguous_titles_skipped': len(ambiguous)}
