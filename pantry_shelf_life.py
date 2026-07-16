"""
Shelf-life classification for the pantry feature (pantry.py), mirroring
flavor_tagging.py's structure: a fixed taxonomy, batched LLM classification
against a local Ollama chat model, cached and resumable.

Unlike flavor tagging (0-4 categories per ingredient, since an ingredient
can have several flavor notes at once), shelf life is exactly one category
per ingredient - a thing has one typical shelf life, not several.
"""
import sqlite3

import llm_client

# (category, representative_days, description-for-the-LLM-prompt)
SHELF_LIFE_TAXONOMY = (
    ("highly_perishable", 4, "fresh herbs, leafy greens, fresh seafood/fish, ground meat, sprouts, fresh-cut produce"),
    ("perishable", 10, "dairy (milk, yogurt, soft cheese), most fresh fruits and vegetables, fresh poultry/meat, deli items"),
    ("semi_perishable", 30, "eggs, hard/aged cheese, root vegetables (potatoes, onions, carrots), opened condiments and sauces"),
    ("frozen", 180, "anything typically stored frozen"),
    ("shelf_stable", 365, "canned/jarred goods, dry pasta/rice/grains/legumes, spices, oils, vinegar, sugar, flour, honey"),
)
VALID_CATEGORIES = frozenset(name for name, _, _ in SHELF_LIFE_TAXONOMY)
DAYS_BY_CATEGORY = {name: days for name, days, _ in SHELF_LIFE_TAXONOMY}
DEFAULT_CATEGORY = "semi_perishable"  # used for anything never classified


def init_shelf_life_table(db_path: str = 'recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ingredient_shelf_life (
            ingredient TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            days INTEGER NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ingredient_shelf_life_tagged (
            ingredient TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()


def _build_prompt(batch: list) -> str:
    lines = "\n".join(f"{i + 1}. {ing}" for i, ing in enumerate(batch))
    category_lines = "\n".join(f'- "{name}" ({days} days): {desc}' for name, days, desc in SHELF_LIFE_TAXONOMY)
    return (
        "Valid shelf-life categories, with a representative number of days "
        f"a household would keep an opened/fresh item like it, and examples:\n{category_lines}\n\n"
        "For each numbered ingredient below (raw recipe text, may include quantities/units/"
        "prep notes), pick EXACTLY ONE category that best matches how long it stays good "
        "once you have it at home. Use ONLY the category names above, lowercase, exact "
        "spelling. If the string isn't actually a food ingredient (e.g. a footnote), pick "
        '"shelf_stable" as a harmless default.\n\n'
        "Respond with ONLY a JSON object whose keys are the numbers as strings (\"1\", \"2\", "
        "...) and whose values are one category string each. No other text.\n\n" + lines
    )


def tag_shelf_life_batch(batch: list, timeout: int = 120) -> dict:
    """Classify a batch of ingredient strings into SHELF_LIFE_TAXONOMY
    categories via the active LLM provider (llm_client.py). Returns
    {0-based index: category}. Any index missing from the model's
    response, or a response that fails to parse, is simply absent -
    callers should treat a missing index as 'retry later', matching
    flavor_tagging.py's convention."""
    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": _build_prompt(batch)}],
            timeout=timeout,
        )
    except llm_client.LLMUnavailableError as e:
        print(f"Error tagging shelf-life batch: {e}")
        return {}

    tagged = {}
    for key, value in parsed.items():
        try:
            idx = int(key) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(batch)) or not isinstance(value, str):
            continue
        if value in VALID_CATEGORIES:
            tagged[idx] = value
    return tagged


def tag_all_ingredients(db_path: str = 'recipes.db', batch_size: int = 30) -> int:
    """LLM-tags every ingredient in ingredient_embeddings (the same
    corpus-frequency-floored candidate pool flavor_tagging.py uses) against
    the shelf-life taxonomy. Safely resumable - skips ingredients already
    marked done, commits after every batch."""
    init_shelf_life_table(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT ingredient FROM ingredient_embeddings')
    candidates = [row[0] for row in cursor.fetchall()]

    cursor.execute('SELECT ingredient FROM ingredient_shelf_life_tagged')
    already_done = set(row[0] for row in cursor.fetchall())

    remaining = [ing for ing in candidates if ing not in already_done]
    tagged_count = 0

    for start in range(0, len(remaining), batch_size):
        batch = remaining[start:start + batch_size]
        categories_by_index = tag_shelf_life_batch(batch)

        for i, ingredient in enumerate(batch):
            category = categories_by_index.get(i)
            if category:
                cursor.execute(
                    'INSERT OR REPLACE INTO ingredient_shelf_life (ingredient, category, days) VALUES (?, ?, ?)',
                    (ingredient, category, DAYS_BY_CATEGORY[category]),
                )
                cursor.execute(
                    'INSERT OR IGNORE INTO ingredient_shelf_life_tagged (ingredient) VALUES (?)',
                    (ingredient,),
                )
                tagged_count += 1

        conn.commit()
        if tagged_count % 300 == 0 and tagged_count:
            print(f"Tagged {tagged_count}/{len(remaining)}...")

    conn.commit()
    conn.close()
    return tagged_count


def get_shelf_life(ingredient: str, db_path: str = 'recipes.db') -> dict:
    """Returns {'category', 'days'} for a canonical ingredient name. Falls
    back to DEFAULT_CATEGORY (semi_perishable, 30 days) for anything never
    tagged - a moderate, harmless assumption rather than either "lasts
    forever" or "always needs confirming tomorrow"."""
    init_shelf_life_table(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        'SELECT category, days FROM ingredient_shelf_life WHERE ingredient = ?', (ingredient.strip().lower(),)
    ).fetchone()
    conn.close()
    if row is None:
        return {'category': DEFAULT_CATEGORY, 'days': DAYS_BY_CATEGORY[DEFAULT_CATEGORY]}
    return {'category': row[0], 'days': row[1]}


if __name__ == '__main__':
    total = tag_all_ingredients()
    print(f"Final count tagged this run: {total}")
