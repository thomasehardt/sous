import json
import sqlite3
from typing import Dict, List, Optional

# Sous has no multi-user/auth concept anywhere in the schema - it's a single
# household's recipe box. Preferences are therefore one singleton row (id=1),
# not per-user rows.
_SINGLETON_ID = 1


def init_preferences_table(db_path: str = 'recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            dietary_restrictions TEXT NOT NULL DEFAULT '[]',
            disliked_ingredients TEXT NOT NULL DEFAULT '[]',
            notes TEXT NOT NULL DEFAULT '',
            hide_builtin_recipes INTEGER NOT NULL DEFAULT 0,
            llm_provider TEXT NOT NULL DEFAULT '',
            llm_model TEXT NOT NULL DEFAULT '',
            ollama_host TEXT NOT NULL DEFAULT ''
        )
    ''')
    existing_columns = {row[1] for row in conn.execute('PRAGMA table_info(preferences)')}
    if 'hide_builtin_recipes' not in existing_columns:
        conn.execute('ALTER TABLE preferences ADD COLUMN hide_builtin_recipes INTEGER NOT NULL DEFAULT 0')
    if 'llm_provider' not in existing_columns:
        # '' means "no override" - llm_client.py falls through to the
        # SOUS_LLM_PROVIDER env var, then the Ollama default. Never a
        # secret (that's API keys, in llm_credentials.py instead) - just
        # a provider id and model name, safe to sit alongside the rest of
        # this git-tracked db.
        conn.execute("ALTER TABLE preferences ADD COLUMN llm_provider TEXT NOT NULL DEFAULT ''")
    if 'llm_model' not in existing_columns:
        conn.execute("ALTER TABLE preferences ADD COLUMN llm_model TEXT NOT NULL DEFAULT ''")
    if 'ollama_host' not in existing_columns:
        # '' means "no override" - llm_client.py falls through to the
        # OLLAMA_HOST env var, then the hardcoded default. A URL, not a
        # secret, so this (unlike API keys) is fine in the git-tracked db.
        conn.execute("ALTER TABLE preferences ADD COLUMN ollama_host TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()


def get_preferences(db_path: str = 'recipes.db') -> Dict:
    """Returns the household's dietary restrictions, disliked ingredients, free-text
    rules/guidelines, and display/LLM settings. Always returns a dict (defaults to
    empty/off) even if never saved - callers shouldn't need to handle None."""
    init_preferences_table(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        'SELECT dietary_restrictions, disliked_ingredients, notes, hide_builtin_recipes, '
        'llm_provider, llm_model, ollama_host FROM preferences WHERE id = ?',
        (_SINGLETON_ID,),
    ).fetchone()
    conn.close()
    if row is None:
        return {
            'dietary_restrictions': [], 'disliked_ingredients': [], 'notes': '',
            'hide_builtin_recipes': False, 'llm_provider': '', 'llm_model': '', 'ollama_host': '',
        }
    dietary_restrictions, disliked_ingredients, notes, hide_builtin_recipes, llm_provider, llm_model, ollama_host = row
    return {
        'dietary_restrictions': json.loads(dietary_restrictions),
        'disliked_ingredients': json.loads(disliked_ingredients),
        'notes': notes,
        'hide_builtin_recipes': bool(hide_builtin_recipes),
        'llm_provider': llm_provider,
        'llm_model': llm_model,
        'ollama_host': ollama_host,
    }


def save_preferences(
    dietary_restrictions: Optional[List[str]] = None,
    disliked_ingredients: Optional[List[str]] = None,
    notes: Optional[str] = None,
    hide_builtin_recipes: Optional[bool] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    ollama_host: Optional[str] = None,
    db_path: str = 'recipes.db',
) -> Dict:
    """dietary_restrictions/disliked_ingredients/notes are full-replace (omit -> cleared),
    matching this function's pre-existing behavior. hide_builtin_recipes/llm_provider/
    llm_model/ollama_host instead preserve their current stored value when omitted (None) -
    callers like the v1 API's _api_update_preferences save the other three fields without
    knowing about these, and full-replace semantics there would silently reset them on
    every unrelated preferences update."""
    init_preferences_table(db_path)
    dietary_restrictions = [d.strip().lower() for d in (dietary_restrictions or []) if d and d.strip()]
    disliked_ingredients = [d.strip().lower() for d in (disliked_ingredients or []) if d and d.strip()]
    notes = (notes or '').strip()

    conn = sqlite3.connect(db_path)
    current = conn.execute(
        'SELECT hide_builtin_recipes, llm_provider, llm_model, ollama_host FROM preferences WHERE id = ?',
        (_SINGLETON_ID,),
    ).fetchone()
    if hide_builtin_recipes is None:
        hide_builtin_recipes = bool(current[0]) if current else False
    if llm_provider is None:
        llm_provider = current[1] if current else ''
    if llm_model is None:
        llm_model = current[2] if current else ''
    if ollama_host is None:
        ollama_host = current[3] if current else ''
    conn.execute('''
        INSERT INTO preferences
            (id, dietary_restrictions, disliked_ingredients, notes, hide_builtin_recipes,
             llm_provider, llm_model, ollama_host)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            dietary_restrictions = excluded.dietary_restrictions,
            disliked_ingredients = excluded.disliked_ingredients,
            notes = excluded.notes,
            hide_builtin_recipes = excluded.hide_builtin_recipes,
            llm_provider = excluded.llm_provider,
            llm_model = excluded.llm_model,
            ollama_host = excluded.ollama_host
    ''', (
        _SINGLETON_ID, json.dumps(dietary_restrictions), json.dumps(disliked_ingredients), notes,
        int(hide_builtin_recipes), llm_provider.strip(), llm_model.strip(), ollama_host.strip(),
    ))
    conn.commit()
    conn.close()
    return {
        'dietary_restrictions': dietary_restrictions,
        'disliked_ingredients': disliked_ingredients,
        'notes': notes,
        'hide_builtin_recipes': hide_builtin_recipes,
        'llm_provider': llm_provider,
        'llm_model': llm_model,
        'ollama_host': ollama_host,
    }


def recipe_conflicts_with_preferences(recipe_id: int, db_path: str = 'recipes.db') -> List[str]:
    """Returns a list of human-readable reasons this recipe conflicts with the saved
    disliked-ingredients list (dietary_restrictions/notes are free-form and not checkable
    against ingredient text alone, so this only checks the structured disliked list)."""
    prefs = get_preferences(db_path)
    disliked = set(prefs['disliked_ingredients'])
    if not disliked:
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        'SELECT DISTINCT name FROM recipe_ingredients WHERE recipe_id = ? AND name IS NOT NULL',
        (recipe_id,),
    ).fetchall()
    conn.close()
    names = {r[0].strip().lower() for r in rows if r[0]}
    hits = sorted(names & disliked)
    return [f'contains {ingredient}' for ingredient in hits]
