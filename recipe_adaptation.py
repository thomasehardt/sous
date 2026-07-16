import sqlite3
from typing import Dict, List, Optional

from preferences import get_preferences
import llm_client


def _canonical_ingredients_for_recipe(db_path: str, recipe_id: int) -> List[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        'SELECT DISTINCT name FROM recipe_ingredients WHERE recipe_id = ? AND name IS NOT NULL',
        (recipe_id,),
    ).fetchall()
    conn.close()
    return sorted({r[0].strip().lower() for r in rows if r[0]})


def _stem(word: str) -> str:
    """Crude plural-insensitive stem (strip trailing 'es'/'s') so 'blueberries' and
    'blueberry'/'dried blueberry' are recognized as the same ingredient - the substring
    check in top_embedding_similar_ingredients doesn't catch this since plural forms often
    diverge in their last few letters (blueberries vs blueberry) rather than being a clean
    substring of one another."""
    word = word.strip().lower()
    if word.endswith('ies'):
        return word[:-3] + 'y'
    if word.endswith('es'):
        return word[:-2]
    if word.endswith('s'):
        return word[:-1]
    return word


def _same_ingredient(a: str, b: str) -> bool:
    stem_a, stem_b = _stem(a), _stem(b)
    return stem_a == stem_b or stem_a in stem_b or stem_b in stem_a


def suggest_substitutions(
    recipe_id: int, meal_db, db_path: str = 'recipes.db', limit_per_ingredient: int = 3
) -> Dict[str, List[Dict]]:
    """For each of this recipe's ingredients flagged as disliked in saved preferences,
    suggest embedding-nearest substitutes (see meal_planner.top_embedding_similar_ingredients)
    that aren't themselves disliked. Returns {ingredient: [{'ingredient', 'similarity'}, ...]},
    empty if there's nothing disliked or no embedding data for the flagged ingredient."""
    prefs = get_preferences(db_path)
    disliked = set(prefs['disliked_ingredients'])
    if not disliked:
        return {}

    names = _canonical_ingredients_for_recipe(db_path, recipe_id)
    flagged = sorted(set(names) & disliked)

    suggestions = {}
    for ingredient in flagged:
        candidates = meal_db.top_embedding_similar_ingredients(ingredient, limit=limit_per_ingredient + len(disliked) + 5)
        filtered = [
            c for c in candidates
            if c['ingredient'] not in disliked and not _same_ingredient(c['ingredient'], ingredient)
        ][:limit_per_ingredient]
        if filtered:
            suggestions[ingredient] = filtered
    return suggestions


def _build_adapt_prompt(recipe, prefs: Dict) -> str:
    ingredients_lines = "\n".join(f"- {line}" for line in recipe.ingredients)
    instructions_lines = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(recipe.instructions))
    pref_bits = []
    if prefs['dietary_restrictions']:
        pref_bits.append(f"Dietary restrictions: {', '.join(prefs['dietary_restrictions'])}")
    if prefs['disliked_ingredients']:
        pref_bits.append(f"Disliked ingredients (must not appear): {', '.join(prefs['disliked_ingredients'])}")
    if prefs['notes']:
        pref_bits.append(f"Other guidelines: {prefs['notes']}")
    pref_text = "\n".join(pref_bits) if pref_bits else "(no restrictions given)"

    return (
        "You are adapting an EXISTING recipe to fit a household's preferences - you are "
        "not inventing a new recipe. Make the smallest changes that satisfy the "
        "preferences below (ingredient substitutions, dropped/added ingredients, or "
        "adjusted method/instructions where a substitution changes technique). Keep the "
        "recipe's overall identity and structure intact wherever possible.\n\n"
        f"Household preferences:\n{pref_text}\n\n"
        f"Original title: {recipe.title}\n\n"
        f"Original ingredients:\n{ingredients_lines}\n\n"
        f"Original instructions:\n{instructions_lines}\n\n"
        "Respond with ONLY a JSON object with these fields:\n"
        '- "title": adapted recipe title (change it only if the swap is significant, '
        'e.g. "Beef Chili" -> "Turkey Chili")\n'
        '- "ingredients": full adapted ingredient list, array of strings, same "quantity '
        'unit name" style as the original lines\n'
        '- "instructions": full adapted instructions, array of strings, one step each\n'
        '- "changes_summary": a short (1-3 sentence) plain-English summary of what you '
        "changed and why\n"
        "No other text."
    )


def adapt_recipe_to_preferences(recipe, db_path: str = 'recipes.db', timeout: int = 90) -> Optional[Dict]:
    """Ask the active LLM provider (llm_client.py) to rewrite a recipe's ingredients/
    instructions to respect saved preferences, grounded in the original recipe
    (substitutions/method tweaks) rather than inventing something unrelated. Returns None
    on any failure (provider unreachable, bad JSON, missing fields) - callers should treat
    None as 'adaptation unavailable right now'."""
    prefs = get_preferences(db_path)
    if not (prefs['dietary_restrictions'] or prefs['disliked_ingredients'] or prefs['notes']):
        return None

    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": _build_adapt_prompt(recipe, prefs)}],
            timeout=timeout,
        )
        title = parsed['title']
        ingredients = [str(i) for i in parsed['ingredients'] if str(i).strip()]
        instructions = [str(s) for s in parsed['instructions'] if str(s).strip()]
        changes_summary = parsed.get('changes_summary', '')
        if not (title and ingredients and instructions):
            raise ValueError('missing required field(s) in LLM response')
    except Exception as e:
        print(f"Error adapting recipe {recipe.id}: {e}")
        return None

    return {
        'title': title,
        'ingredients': ingredients,
        'instructions': instructions,
        'changes_summary': changes_summary,
    }
