import sqlite3
from typing import Dict, List, Optional

from preferences import get_preferences
from query_planner import plan_intent_query
import llm_client


def _seed_ingredients_from_mood(mood: str, db_path: str, per_flavor: int = 2) -> List[str]:
    """When no explicit ingredients are given, derive some from the mood text via the same
    flavor-category planner used for /craving, then pick the most common real ingredients
    tagged with each flavor (by ingredient_totals) as starting seeds for the palette."""
    plan = plan_intent_query(mood)
    flavors = plan['flavors']
    if not flavors:
        return []
    conn = sqlite3.connect(db_path)
    seeds: List[str] = []
    for flavor in flavors:
        rows = conn.execute('''
            SELECT f.ingredient, COALESCE(t.total_count, 0) as total_count
            FROM ingredient_flavors f
            LEFT JOIN ingredient_totals t ON t.ingredient = f.ingredient
            WHERE f.flavor = ?
            ORDER BY total_count DESC
            LIMIT ?
        ''', (flavor, per_flavor)).fetchall()
        seeds.extend(r[0] for r in rows)
    conn.close()
    seen = set()
    deduped = []
    for s in seeds:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def build_ingredient_palette(
    seed_ingredients: List[str], meal_db, db_path: str = 'recipes.db',
    top_n_per_seed: int = 8, max_total: int = 25,
) -> List[str]:
    """Expand seed ingredients into a grounded candidate palette using real ingredient
    co-occurrence stats (ingredient_pairs, built from what actually appears together across
    the whole recipe corpus) rather than letting the LLM freely associate. Seeds themselves
    are always included (unless disliked); companions are ranked by how often they've
    actually co-occurred with a seed in a real recipe. Disliked ingredients are excluded
    entirely so invention never has to be told "no" after the fact."""
    prefs = get_preferences(db_path)
    disliked = set(prefs['disliked_ingredients'])

    scores: Dict[str, int] = {}
    seeds_normed = [s.strip().lower() for s in seed_ingredients if s and s.strip()]
    for seed in seeds_normed:
        if seed in disliked:
            continue
        scores[seed] = max(scores.get(seed, 0), 10 ** 6)  # seeds always rank first
        for pair in meal_db.top_pairs_for_ingredient(seed, limit=top_n_per_seed):
            ing = pair['ingredient']
            if ing in disliked:
                continue
            scores[ing] = max(scores.get(ing, 0), pair['count'])

    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return [ing for ing, _ in ordered[:max_total]]


def _build_invent_prompt(seed_ingredients: List[str], palette: List[str], mood: str, prefs: Dict) -> str:
    pref_bits = []
    if prefs['dietary_restrictions']:
        pref_bits.append(f"Dietary restrictions: {', '.join(prefs['dietary_restrictions'])}")
    if prefs['disliked_ingredients']:
        pref_bits.append(f"Disliked ingredients (must not appear): {', '.join(prefs['disliked_ingredients'])}")
    if prefs['notes']:
        pref_bits.append(f"Other guidelines: {prefs['notes']}")
    pref_text = "\n".join(pref_bits) if pref_bits else "(no restrictions given)"

    mood_line = f'\nThe cook is in the mood for: "{mood}"\n' if mood.strip() else ""
    seed_line = f"Ingredients on hand (use these): {', '.join(seed_ingredients)}\n" if seed_ingredients else ""

    return (
        "Invent an original recipe. Do not copy an existing named dish verbatim - it's "
        "fine to be inspired by a known style, but write your own version.\n\n"
        f"{seed_line}"
        "A candidate ingredient palette, built from ingredients that actually co-occur "
        "together often in real recipes (so the combinations are proven, not just "
        "plausible-sounding): "
        f"{', '.join(palette)}\n"
        "Draw most of the recipe's ingredients from this palette. You may add a small "
        "number of basic pantry staples (salt, oil, water, flour, etc.) not in the list if "
        "needed, but don't introduce unrelated main ingredients that aren't in the "
        "palette.\n"
        f"{mood_line}\n"
        f"Household preferences:\n{pref_text}\n\n"
        "Respond with ONLY a JSON object with these fields:\n"
        '- "title": a specific, appetizing recipe title (not generic like "Tasty Dinner")\n'
        '- "description": 1-2 sentence description\n'
        '- "ingredients": array of strings, each "quantity unit ingredient" (e.g. "2 cups '
        'diced onion")\n'
        '- "instructions": array of strings, one step each\n'
        '- "cuisine": a single cuisine label if one clearly fits, else null\n'
        "No other text."
    )


def invent_recipe(
    seed_ingredients: Optional[List[str]] = None, mood: str = '',
    meal_db=None, db_path: str = 'recipes.db', timeout: int = 90,
) -> Optional[Dict]:
    """Generate a brand-new recipe grounded in real ingredient co-occurrence statistics
    (not freeform hallucination - contrast with easter_egg.py's comedic riffs, which don't
    aim for a usable result) and the household's saved preferences, via the active LLM
    provider (llm_client.py). Returns None on any failure (no seeds/palette, provider
    unreachable, bad JSON)."""
    seed_ingredients = seed_ingredients or []
    if not seed_ingredients and mood.strip():
        seed_ingredients = _seed_ingredients_from_mood(mood, db_path)
    palette = build_ingredient_palette(seed_ingredients, meal_db, db_path=db_path)
    if not palette:
        return None

    prefs = get_preferences(db_path)
    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": _build_invent_prompt(seed_ingredients, palette, mood, prefs)}],
            timeout=timeout,
        )
        title = parsed['title']
        description = parsed.get('description', '')
        ingredients = [str(i) for i in parsed['ingredients'] if str(i).strip()]
        instructions = [str(s) for s in parsed['instructions'] if str(s).strip()]
        cuisine = parsed.get('cuisine')
        cuisine = cuisine if isinstance(cuisine, str) and cuisine.strip() else None
        if not (title and ingredients and instructions):
            raise ValueError('missing required field(s) in LLM response')
    except Exception as e:
        print(f"Error inventing recipe: {e}")
        return None

    return {
        'title': title,
        'description': description,
        'ingredients': ingredients,
        'instructions': instructions,
        'cuisine': cuisine,
        'grounded_in': palette,
    }
