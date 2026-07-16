import json
import sqlite3

import llm_client


def get_embedding(text: str) -> list[float]:
    """Thin wrapper kept for this module's existing call sites/tests -
    see llm_client.embed() for the actual implementation (Ollama-only
    regardless of the active chat provider; see its docstring for why)."""
    return llm_client.embed(text)

def cosine_similarity(a: list[float], b: list[float]) -> float:
    # Calculate dot product
    dot_product = sum(x * y for x, y in zip(a, b))
    
    # Calculate magnitudes
    magnitude_a = sum(x * x for x in a) ** 0.5
    magnitude_b = sum(x * x for x in b) ** 0.5
    
    # Avoid division by zero
    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0
    
    return dot_product / (magnitude_a * magnitude_b)

def build_ingredient_embeddings(db_path='recipes.db', min_count=3):
    """Embed every ingredient with total_count >= min_count and cache the
    vectors in ingredient_embeddings. Safely resumable - skips ingredients
    that already have a stored vector, commits every 50 so an interruption
    doesn't lose progress."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ingredient_embeddings (
            ingredient TEXT PRIMARY KEY,
            vector TEXT
        )
    ''')
    conn.commit()

    cursor.execute('SELECT ingredient FROM ingredient_totals WHERE total_count >= ?', (min_count,))
    candidates = [row[0] for row in cursor.fetchall()]

    cursor.execute('SELECT ingredient FROM ingredient_embeddings')
    already_done = set(row[0] for row in cursor.fetchall())

    remaining = [ing for ing in candidates if ing not in already_done]
    embedded_count = 0

    for ingredient in remaining:
        vector = get_embedding(ingredient)
        if vector:
            cursor.execute(
                'INSERT INTO ingredient_embeddings (ingredient, vector) VALUES (?, ?)',
                (ingredient, json.dumps(vector))
            )
            embedded_count += 1

        if embedded_count % 50 == 0 and embedded_count > 0:
            conn.commit()
        if embedded_count % 200 == 0 and embedded_count > 0:
            print(f"Embedded {embedded_count}/{len(remaining)}...")

    conn.commit()
    conn.close()
    return embedded_count


if __name__ == '__main__':
    total = build_ingredient_embeddings()
    print(f"Final count embedded this run: {total}")