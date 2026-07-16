import sqlite3
from collections import Counter
from typing import Dict, List, Optional


def _canonical_ingredients_for_recipe(conn: sqlite3.Connection, recipe_id: int) -> List[str]:
    """Canonical parsed ingredient names for a recipe (recipe_ingredients.name),
    not the raw recipes.ingredients text - the raw text often carries a quantity
    prefix (e.g. "4 blueberries") that never matches ingredient_flavors rows,
    which are keyed on the bare parsed name (e.g. "blueberries")."""
    rows = conn.execute(
        "SELECT DISTINCT name FROM recipe_ingredients WHERE recipe_id = ? AND name IS NOT NULL",
        (recipe_id,),
    ).fetchall()
    return sorted({r[0].strip().lower() for r in rows if r[0] and r[0].strip()})


def _flavor_counts_for_ingredients(conn: sqlite3.Connection, ingredients: List[str]) -> Counter:
    counts: Counter = Counter()
    if not ingredients:
        return counts
    placeholders = ",".join("?" for _ in ingredients)
    rows = conn.execute(
        f"SELECT flavor FROM ingredient_flavors WHERE ingredient IN ({placeholders})",
        ingredients,
    ).fetchall()
    for (flavor,) in rows:
        counts[flavor] += 1
    return counts


def get_ingredient_flavor_profile(ingredient: str, db_path: str = "recipes.db") -> Dict:
    """Flavor tags for a single normalized ingredient string."""
    conn = sqlite3.connect(db_path)
    normed = ingredient.strip().lower()
    flavors = [
        row[0]
        for row in conn.execute(
            "SELECT flavor FROM ingredient_flavors WHERE ingredient = ? ORDER BY flavor", (normed,)
        ).fetchall()
    ]
    tagged = conn.execute(
        "SELECT 1 FROM ingredient_flavor_tagged WHERE ingredient = ?", (normed,)
    ).fetchone() is not None
    conn.close()
    return {"ingredient": normed, "flavors": flavors, "tagged": tagged}


def get_recipe_flavor_profile(recipe_id: int, db_path: str = "recipes.db") -> Optional[Dict]:
    """Aggregate flavor counts across a recipe's ingredients."""
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        conn.close()
        return None
    ingredients = _canonical_ingredients_for_recipe(conn, recipe_id)
    counts = _flavor_counts_for_ingredients(conn, ingredients)
    tagged_rows = conn.execute(
        f"SELECT ingredient FROM ingredient_flavor_tagged WHERE ingredient IN ({','.join('?' for _ in ingredients)})",
        ingredients,
    ).fetchall() if ingredients else []
    tagged_set = {r[0] for r in tagged_rows}
    conn.close()
    return {
        "recipe_id": recipe_id,
        "ingredient_count": len(ingredients),
        "untagged_ingredients": [i for i in ingredients if i not in tagged_set],
        "flavor_counts": dict(counts),
    }


def get_cuisine_flavor_profile(cuisine: str, db_path: str = "recipes.db", limit: Optional[int] = None) -> Dict:
    """Aggregate flavor counts across all recipes matching a cuisine (comma-separated
    multi-value field - matches if `cuisine` appears as one of the comma-split values,
    not just via substring/exact match on the raw column)."""
    conn = sqlite3.connect(db_path)
    target = cuisine.strip().lower()
    rows = conn.execute(
        "SELECT id, cuisine FROM recipes WHERE cuisine LIKE ?", (f"%{target}%",)
    ).fetchall()

    recipe_count = 0
    counts: Counter = Counter()
    for recipe_id, raw_cuisine in rows:
        components = [c.strip().lower() for c in (raw_cuisine or "").split(",")]
        if target not in components:
            continue
        recipe_count += 1
        if limit and recipe_count > limit:
            recipe_count -= 1
            break
        ingredients = _canonical_ingredients_for_recipe(conn, recipe_id)
        counts.update(_flavor_counts_for_ingredients(conn, ingredients))
    conn.close()
    return {"cuisine": target, "recipe_count": recipe_count, "flavor_counts": dict(counts)}


def get_meal_plan_flavor_profile(plan_id: int, db_path: str = "recipes.db") -> Optional[Dict]:
    """Aggregate flavor counts across every recipe currently in a meal plan."""
    conn = sqlite3.connect(db_path)
    plan_row = conn.execute("SELECT id FROM meal_plans WHERE id = ?", (plan_id,)).fetchone()
    if plan_row is None:
        conn.close()
        return None
    recipe_ids = [
        r[0]
        for r in conn.execute(
            "SELECT recipe_id FROM meal_plan_items WHERE meal_plan_id = ?", (plan_id,)
        ).fetchall()
    ]
    conn.close()

    combined: Counter = Counter()
    per_recipe = {}
    for recipe_id in recipe_ids:
        profile = get_recipe_flavor_profile(recipe_id, db_path=db_path)
        if profile is None:
            continue
        per_recipe[recipe_id] = profile["flavor_counts"]
        combined.update(profile["flavor_counts"])

    return {
        "plan_id": plan_id,
        "recipe_ids": recipe_ids,
        "flavor_counts": dict(combined),
        "per_recipe": per_recipe,
    }
