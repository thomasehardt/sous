"""
Shopping lists for Sous: generate from a recipe's or meal plan's structured
ingredients, merge quantities across recipes that share an ingredient, and
track checked-off state while shopping.

Honesty note on merging: two lines only get combined into one quantity when
they share the same canonical ingredient name AND the same unit (both
already normalized by recipe_scaling.parse_ingredient()). "2 cups flour" and
"3 tbsp flour" stay as two separate lines rather than being cross-unit
converted - there's no reliable unit-conversion table in this project, and a
wrong silent conversion would be worse than two honest lines. An ingredient
with no parsed quantity at all (heuristic parse failure) is never merged -
it's added as its own line using the original raw text.
"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from recipe_scaling import format_structured_quantity


def init_shopping_lists_table(db_path: str = 'recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS shopping_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS shopping_list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            quantity REAL,
            unit TEXT,
            checked INTEGER NOT NULL DEFAULT 0,
            source_recipe_id INTEGER,
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (list_id) REFERENCES shopping_lists(id)
        )
    ''')
    conn.commit()
    conn.close()


def create_list(name: str, db_path: str = 'recipes.db') -> int:
    init_shopping_lists_table(db_path)
    now = datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        'INSERT INTO shopping_lists (name, created_at, updated_at) VALUES (?, ?, ?)',
        (name, now, now),
    )
    list_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return list_id


def get_list(list_id: int, db_path: str = 'recipes.db') -> Optional[Dict]:
    init_shopping_lists_table(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        'SELECT id, name, created_at, updated_at FROM shopping_lists WHERE id = ?', (list_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {'id': row[0], 'name': row[1], 'created_at': row[2], 'updated_at': row[3]}


def list_lists(db_path: str = 'recipes.db') -> List[Dict]:
    init_shopping_lists_table(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute('''
        SELECT l.id, l.name, l.created_at, l.updated_at,
               COUNT(i.id) as item_count,
               COALESCE(SUM(i.checked), 0) as checked_count
        FROM shopping_lists l
        LEFT JOIN shopping_list_items i ON i.list_id = l.id
        GROUP BY l.id
        ORDER BY l.updated_at DESC
    ''').fetchall()
    conn.close()
    return [
        {'id': r[0], 'name': r[1], 'created_at': r[2], 'updated_at': r[3], 'item_count': r[4], 'checked_count': r[5]}
        for r in rows
    ]


def delete_list(list_id: int, db_path: str = 'recipes.db') -> bool:
    init_shopping_lists_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute('DELETE FROM shopping_list_items WHERE list_id = ?', (list_id,))
    cursor = conn.execute('DELETE FROM shopping_lists WHERE id = ?', (list_id,))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def get_items(list_id: int, db_path: str = 'recipes.db') -> List[Dict]:
    init_shopping_lists_table(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute('''
        SELECT id, name, quantity, unit, checked, source_recipe_id, position
        FROM shopping_list_items WHERE list_id = ? ORDER BY checked ASC, position ASC, id ASC
    ''', (list_id,)).fetchall()
    conn.close()
    items = []
    for r in rows:
        item_id, name, quantity, unit, checked, source_recipe_id, position = r
        items.append({
            'id': item_id,
            'name': name,
            'quantity': quantity,
            'unit': unit,
            'display': format_structured_quantity(quantity, unit) + f' {name}' if quantity is not None else name,
            'checked': bool(checked),
            'source_recipe_id': source_recipe_id,
        })
    return items


def _touch_list(conn, list_id: int):
    conn.execute('UPDATE shopping_lists SET updated_at = ? WHERE id = ?', (datetime.now().isoformat(), list_id))


def add_manual_item(list_id: int, name: str, quantity: Optional[float] = None, unit: Optional[str] = None,
                     db_path: str = 'recipes.db') -> int:
    init_shopping_lists_table(db_path)
    conn = sqlite3.connect(db_path)
    max_pos = conn.execute('SELECT COALESCE(MAX(position), -1) FROM shopping_list_items WHERE list_id = ?', (list_id,)).fetchone()[0]
    cursor = conn.execute(
        'INSERT INTO shopping_list_items (list_id, name, quantity, unit, position) VALUES (?, ?, ?, ?, ?)',
        (list_id, name.strip(), quantity, unit, max_pos + 1),
    )
    item_id = cursor.lastrowid
    _touch_list(conn, list_id)
    conn.commit()
    conn.close()
    return item_id


def remove_item(item_id: int, db_path: str = 'recipes.db') -> bool:
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT list_id FROM shopping_list_items WHERE id = ?', (item_id,)).fetchone()
    if row is None:
        conn.close()
        return False
    cursor = conn.execute('DELETE FROM shopping_list_items WHERE id = ?', (item_id,))
    _touch_list(conn, row[0])
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_item_name(item_id: int, db_path: str = 'recipes.db') -> Optional[str]:
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT name FROM shopping_list_items WHERE id = ?', (item_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_item_checked(item_id: int, checked: bool, db_path: str = 'recipes.db') -> bool:
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT list_id FROM shopping_list_items WHERE id = ?', (item_id,)).fetchone()
    if row is None:
        conn.close()
        return False
    cursor = conn.execute('UPDATE shopping_list_items SET checked = ? WHERE id = ?', (1 if checked else 0, item_id))
    _touch_list(conn, row[0])
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def _merge_or_insert(conn, list_id: int, name: str, quantity: Optional[float], unit: Optional[str], source_recipe_id: Optional[int]):
    """Adds one structured ingredient to a list, combining it into an
    existing unchecked line if one shares the same (name, unit) and both
    have a parseable quantity. Ingredients with no parsed quantity are
    never merged - each becomes its own line."""
    name = name.strip()
    if quantity is not None:
        existing = conn.execute('''
            SELECT id, quantity FROM shopping_list_items
            WHERE list_id = ? AND LOWER(name) = LOWER(?) AND IFNULL(unit, '') = IFNULL(?, '')
              AND checked = 0 AND quantity IS NOT NULL
            LIMIT 1
        ''', (list_id, name, unit)).fetchone()
        if existing:
            item_id, existing_qty = existing
            conn.execute('UPDATE shopping_list_items SET quantity = ? WHERE id = ?', (existing_qty + quantity, item_id))
            return item_id

    max_pos = conn.execute('SELECT COALESCE(MAX(position), -1) FROM shopping_list_items WHERE list_id = ?', (list_id,)).fetchone()[0]
    cursor = conn.execute(
        'INSERT INTO shopping_list_items (list_id, name, quantity, unit, source_recipe_id, position) VALUES (?, ?, ?, ?, ?, ?)',
        (list_id, name, quantity, unit, source_recipe_id, max_pos + 1),
    )
    return cursor.lastrowid


def add_recipe_to_list(list_id: int, recipe_id: int, recipe_db, servings: Optional[int] = None,
                        db_path: str = 'recipes.db') -> int:
    """Adds every ingredient of a recipe to a shopping list, merging into
    existing unchecked lines where name+unit match. If `servings` is given,
    quantities are scaled to that target first (same factor math as recipe
    scaling elsewhere in the app). Returns the number of ingredient lines
    processed (merged or newly inserted)."""
    init_shopping_lists_table(db_path)
    structured = recipe_db.get_structured_ingredients(recipe_id)
    if not structured:
        return 0

    factor = 1.0
    if servings:
        recipe = recipe_db.get_recipe(recipe_id)
        current_servings = recipe.servings if recipe and recipe.servings > 0 else 1
        factor = servings / current_servings

    conn = sqlite3.connect(db_path)
    count = 0
    for ing in structured:
        if ing.get('is_section_header'):
            continue  # "For the Crust:" etc. - a label, not something to buy
        name = ing.get('name') or ing.get('raw_text')
        quantity = ing['quantity'] * factor if ing.get('quantity') is not None else None
        _merge_or_insert(conn, list_id, name, quantity, ing.get('unit'), recipe_id)
        count += 1
    _touch_list(conn, list_id)
    conn.commit()
    conn.close()
    return count


def add_plan_to_list(list_id: int, plan_id: int, meal_db, recipe_db, db_path: str = 'recipes.db') -> int:
    """Adds every recipe in a meal plan to a shopping list, one recipe at a
    time through add_recipe_to_list() so identical ingredients across
    different recipes in the plan still merge into one line."""
    recipe_ids = meal_db.get_plan_recipe_ids(plan_id)
    total = 0
    for recipe_id in recipe_ids:
        total += add_recipe_to_list(list_id, recipe_id, recipe_db, db_path=db_path)
    return total
