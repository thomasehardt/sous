import sqlite3

from flavor_taxonomy import FLAVOR_TAXONOMY
import llm_client

VALID_FLAVORS = frozenset(name for name, _, _ in FLAVOR_TAXONOMY)


def init_ingredient_flavors_table(db_path='recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ingredient_flavors (
            ingredient TEXT NOT NULL,
            flavor TEXT NOT NULL,
            PRIMARY KEY (ingredient, flavor)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ingredient_flavor_tagged (
            ingredient TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()


def _build_prompt(batch: list[str]) -> str:
    lines = "\n".join(f"{i + 1}. {ing}" for i, ing in enumerate(batch))
    categories = ", ".join(sorted(VALID_FLAVORS))
    return (
        f"Valid flavor categories: {categories}.\n\n"
        "For each numbered ingredient below (raw recipe text, may include quantities/units/"
        "prep notes), list which flavor categories it primarily contributes to a dish. Use "
        "ONLY the category names above, lowercase, exact spelling. If the string isn't "
        "actually a food ingredient (e.g. a footnote), return an empty list for it.\n\n"
        "Respond with ONLY a JSON object whose keys are the numbers as strings (\"1\", \"2\", "
        "...) and whose values are arrays of 0-4 category strings. No other text.\n\n" + lines
    )


def tag_ingredient_batch(batch: list[str], timeout=120) -> dict[int, list[str]]:
    """Tag a batch of ingredient strings against VALID_FLAVORS via the active LLM
    provider (llm_client.py). Returns {0-based index: [valid flavor names]}. Any index
    missing from the model's response, or any response that fails to parse as JSON, is
    simply absent from the returned dict - callers should treat a missing index as
    'retry later', not as an empty tag."""
    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": _build_prompt(batch)}],
            timeout=timeout,
        )
    except llm_client.LLMUnavailableError as e:
        print(f"Error tagging batch: {e}")
        return {}

    tagged = {}
    for key, value in parsed.items():
        try:
            idx = int(key) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(batch)) or not isinstance(value, list):
            continue
        tagged[idx] = [v for v in value if v in VALID_FLAVORS]
    return tagged


def tag_all_ingredients(db_path='recipes.db', batch_size=30):
    """LLM-tag every ingredient in ingredient_embeddings against the flavor taxonomy.
    Safely resumable - skips ingredients already marked done in
    ingredient_flavor_tagged, commits after every batch so an interruption doesn't lose
    progress."""
    init_ingredient_flavors_table(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT ingredient FROM ingredient_embeddings')
    candidates = [row[0] for row in cursor.fetchall()]

    cursor.execute('SELECT ingredient FROM ingredient_flavor_tagged')
    already_done = set(row[0] for row in cursor.fetchall())

    remaining = [ing for ing in candidates if ing not in already_done]
    tagged_count = 0

    for start in range(0, len(remaining), batch_size):
        batch = remaining[start:start + batch_size]
        tags_by_index = tag_ingredient_batch(batch)

        for i, ingredient in enumerate(batch):
            for flavor in tags_by_index.get(i, []):
                cursor.execute(
                    'INSERT OR IGNORE INTO ingredient_flavors (ingredient, flavor) VALUES (?, ?)',
                    (ingredient, flavor)
                )
            cursor.execute(
                'INSERT OR IGNORE INTO ingredient_flavor_tagged (ingredient) VALUES (?)',
                (ingredient,)
            )
            tagged_count += 1

        conn.commit()
        if tagged_count % 300 == 0:
            print(f"Tagged {tagged_count}/{len(remaining)}...")

    conn.commit()
    conn.close()
    return tagged_count


if __name__ == '__main__':
    total = tag_all_ingredients()
    print(f"Final count tagged this run: {total}")
