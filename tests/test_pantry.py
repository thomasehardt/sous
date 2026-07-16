"""
Pantry freshness/decay logic - the second highest-risk area, since the
whole point of the feature (per the explicit requirement it was built to)
is that it must not silently trust stale stock, and must not nag about
items that are still genuinely fresh. Shelf-life values are seeded
directly (not via a real LLM call - no network dependency in tests, and
the classification itself is flavor_tagging.py's/pantry_shelf_life.py's
own concern, already validated separately in PROGRESS.md).
"""
import sqlite3
from datetime import datetime, timedelta

import pantry
from pantry_shelf_life import init_shelf_life_table, DAYS_BY_CATEGORY, DEFAULT_CATEGORY


def _seed_shelf_life(db_path, ingredient, category, days):
    init_shelf_life_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO ingredient_shelf_life (ingredient, category, days) VALUES (?, ?, ?)',
        (ingredient, category, days),
    )
    conn.commit()
    conn.close()


def _insert_pantry_item(db_path, name, days_old, quantity=None, source='manual'):
    pantry.init_pantry_table(db_path)
    added_at = (datetime.now() - timedelta(days=days_old)).isoformat()
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        'INSERT INTO pantry_items (name, quantity, added_at, source) VALUES (?, ?, ?, ?)',
        (name, quantity, added_at, source),
    )
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id


def test_item_well_within_shelf_life_is_fresh(db_path):
    _seed_shelf_life(db_path, "milk", "perishable", 10)
    _insert_pantry_item(db_path, "milk", days_old=1)

    items = pantry.get_items(db_path=db_path)
    assert len(items) == 1
    assert items[0]["status"] == "fresh"


def test_item_at_confirm_threshold_needs_confirmation(db_path):
    """0.8x of a 10-day shelf life = 8 days. At 9 days it should be
    flagged, not silently trusted."""
    _seed_shelf_life(db_path, "milk", "perishable", 10)
    _insert_pantry_item(db_path, "milk", days_old=9)

    items = pantry.get_items(db_path=db_path)
    assert items[0]["status"] == "needs_confirmation"


def test_item_past_discard_threshold_is_auto_removed(db_path):
    """1.5x of a 10-day shelf life = 15 days. At 16 days it should be
    gone entirely by the time get_items() returns - not flagged, not
    listed as expired, just absent."""
    _seed_shelf_life(db_path, "milk", "perishable", 10)
    item_id = _insert_pantry_item(db_path, "milk", days_old=16)

    removed_count = pantry.discard_expired_items(db_path=db_path)
    items = pantry.get_items(db_path=db_path)

    assert removed_count == 1
    assert items == []
    assert pantry.confirm_item(item_id, db_path=db_path) is False  # really gone, not just hidden


def test_discard_only_removes_expired_not_fresh_or_needing_confirmation(db_path):
    _seed_shelf_life(db_path, "milk", "perishable", 10)
    _insert_pantry_item(db_path, "milk", days_old=1)     # fresh
    _insert_pantry_item(db_path, "milk", days_old=9)      # needs_confirmation
    _insert_pantry_item(db_path, "milk", days_old=16)     # expired

    removed = pantry.discard_expired_items(db_path=db_path)
    remaining = pantry.get_items(db_path=db_path)

    assert removed == 1
    assert len(remaining) == 2
    assert {i["status"] for i in remaining} == {"fresh", "needs_confirmation"}


def test_confirmed_fresh_names_excludes_items_needing_confirmation(db_path):
    """This is the exact guarantee /discover's pantry auto-fill relies on
    - an item nearing its shelf life must not be silently assumed
    available just because it's technically still in the table."""
    _seed_shelf_life(db_path, "milk", "perishable", 10)
    _insert_pantry_item(db_path, "milk", days_old=1)
    _seed_shelf_life(db_path, "eggs", "semi_perishable", 30)
    _insert_pantry_item(db_path, "eggs", days_old=25)  # 25/30 = 0.83 -> needs_confirmation

    fresh_names = pantry.get_confirmed_fresh_names(db_path=db_path)
    assert fresh_names == ["milk"]


def test_confirm_item_resets_the_clock(db_path):
    _seed_shelf_life(db_path, "milk", "perishable", 10)
    item_id = _insert_pantry_item(db_path, "milk", days_old=9)

    before = pantry.get_items(db_path=db_path)[0]
    assert before["status"] == "needs_confirmation"

    pantry.confirm_item(item_id, db_path=db_path)

    after = pantry.get_items(db_path=db_path)[0]
    assert after["status"] == "fresh"
    assert after["days_since_added"] == 0.0


def test_unknown_ingredient_falls_back_to_default_category(db_path):
    """No ingredient_shelf_life row exists for this name at all - should
    get the moderate default, not crash and not assume it lasts forever."""
    _insert_pantry_item(db_path, "some totally novel ingredient", days_old=1)

    items = pantry.get_items(db_path=db_path)
    assert items[0]["shelf_life_category"] == DEFAULT_CATEGORY
    assert items[0]["shelf_life_days"] == DAYS_BY_CATEGORY[DEFAULT_CATEGORY]


def test_add_or_refresh_item_dedups_instead_of_duplicating(db_path):
    _seed_shelf_life(db_path, "milk", "perishable", 10)

    first_id = pantry.add_or_refresh_item("milk", source="manual", db_path=db_path)
    assert len(pantry.get_items(db_path=db_path)) == 1

    second_id = pantry.add_or_refresh_item("milk", source="shopping_list", db_path=db_path)
    items = pantry.get_items(db_path=db_path)

    assert second_id == first_id
    assert len(items) == 1
    assert items[0]["source"] == "shopping_list"  # refreshed, not left stale


def test_remove_item(db_path):
    item_id = _insert_pantry_item(db_path, "milk", days_old=1)
    assert pantry.remove_item(item_id, db_path=db_path) is True
    assert pantry.get_items(db_path=db_path) == []
    assert pantry.remove_item(item_id, db_path=db_path) is False  # already gone
