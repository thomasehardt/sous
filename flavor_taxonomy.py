import sqlite3

FLAVOR_TAXONOMY = (
    ("sweet", "basic_taste", "sugary, cloying flavor"),
    ("sour", "basic_taste", "acidic, tart flavor"),
    ("salty", "basic_taste", "savory mineral flavor from salt"),
    ("bitter", "basic_taste", "sharp, acrid flavor"),
    ("umami", "basic_taste", "savory, meaty depth of flavor"),
    ("citrus", "aromatic", "bright, zesty citrus notes"),
    ("earthy", "aromatic", "soil-like, mushroomy, root-vegetable notes"),
    ("smoky", "aromatic", "charred or smoke-infused notes"),
    ("floral", "aromatic", "flower-like, perfumed notes"),
    ("pungent", "aromatic", "sharp, biting aroma (e.g. raw garlic, onion, mustard)"),
    ("spicy_heat", "aromatic", "chili-pepper heat sensation, not a taste"),
    ("herbal", "aromatic", "fresh or dried herb notes"),
    ("nutty", "aromatic", "toasted nut or seed notes"),
    ("fatty_rich", "aromatic", "buttery, oily, mouth-coating richness"),
    ("fresh_green", "aromatic", "grassy, raw-vegetable, uncooked-green notes"),
    ("fermented_funky", "aromatic", "tangy, aged, fermented notes"),
    ("warm_spice", "aromatic", "cinnamon/clove/nutmeg-style warm spice notes"),
)


def init_flavor_taxonomy_table(db_path="recipes.db"):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flavor_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category_group TEXT NOT NULL,
            description TEXT
        )
    """)
    conn.commit()
    conn.close()


def seed_flavor_taxonomy(db_path="recipes.db"):
    init_flavor_taxonomy_table(db_path)
    conn = sqlite3.connect(db_path)
    for name, group, description in FLAVOR_TAXONOMY:
        conn.execute(
            "INSERT OR IGNORE INTO flavor_categories (name, category_group, description) VALUES (?, ?, ?)",
            (name, group, description),
        )
    conn.commit()
    conn.close()


def get_flavor_categories(db_path="recipes.db", group=None):
    conn = sqlite3.connect(db_path)
    if group is None:
        rows = conn.execute(
            "SELECT id, name, category_group, description FROM flavor_categories ORDER BY category_group, name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, category_group, description FROM flavor_categories WHERE category_group = ? ORDER BY name",
            (group,)
        ).fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "category_group": r[2], "description": r[3]} for r in rows]