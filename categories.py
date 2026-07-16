import sqlite3

DB_PATH = "recipes.db"


def init_categories_table(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recipe_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            UNIQUE(recipe_id, category)
        )
    """)
    conn.commit()
    conn.close()


def add_category(recipe_id: int, category: str, db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO recipe_categories (recipe_id, category) VALUES (?, ?)",
        (recipe_id, category),
    )
    conn.commit()
    conn.close()


def get_categories(recipe_id: int, db_path: str = DB_PATH) -> list:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT category FROM recipe_categories WHERE recipe_id = ? ORDER BY category",
        (recipe_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_category_counts(db_path: str = DB_PATH, exclude_builtin: bool = False) -> list:
    """Every category with its recipe count, most-used first. Used by both the
    /categories page and the /api/v1/categories endpoint, so both stay
    consistent with the hide-built-in-recipes preference (otherwise a category
    could show a nonzero count while hiding built-in recipes and then render
    empty once clicked into)."""
    conn = sqlite3.connect(db_path)
    if exclude_builtin:
        rows = conn.execute(
            "SELECT rc.category, COUNT(*) as n FROM recipe_categories rc "
            "JOIN recipes r ON r.id = rc.recipe_id "
            "WHERE r.license = 'user-imported' "
            "GROUP BY rc.category ORDER BY n DESC, rc.category ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT category, COUNT(*) as n FROM recipe_categories GROUP BY category ORDER BY n DESC, category ASC"
        ).fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def get_recipes_by_category(category: str, db_path: str = DB_PATH, exclude_builtin: bool = False) -> list:
    conn = sqlite3.connect(db_path)
    if exclude_builtin:
        rows = conn.execute(
            "SELECT rc.recipe_id FROM recipe_categories rc "
            "JOIN recipes r ON r.id = rc.recipe_id "
            "WHERE rc.category = ? AND r.license = 'user-imported'",
            (category,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT recipe_id FROM recipe_categories WHERE category = ?",
            (category,),
        ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def backfill_from_mislabeled_cuisine(db_path: str = DB_PATH) -> int:
    init_categories_table(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, cuisine FROM recipes WHERE license = 'MIT' AND cuisine != ''"
    ).fetchall()
    count = 0
    for recipe_id, cuisine in rows:
        conn.execute(
            "INSERT OR IGNORE INTO recipe_categories (recipe_id, category) VALUES (?, ?)",
            (recipe_id, cuisine),
        )
        conn.execute("UPDATE recipes SET cuisine = '' WHERE id = ?", (recipe_id,))
        count += 1
    conn.commit()
    conn.close()
    return count