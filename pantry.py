"""
Persistent pantry: what you have on hand, retained across visits rather
than typed in fresh every time (unlike /discover's plain have= list).

Two things make this "intelligent" rather than a flat inventory list, per
the explicit request behind it:
1. Shelf-life-aware decay: an item well past its typical shelf life
   (see pantry_shelf_life.py) is discarded automatically - old knowledge
   doesn't linger forever as if it were still true.
2. Confirmation before assuming: an item approaching or just past its
   shelf life is flagged 'needs_confirmation' rather than either being
   silently trusted or silently dropped - get_confirmed_fresh_names()
   (what /discover's pantry auto-fill uses) excludes these until the user
   says yes or no, so the app never *assumes* stale stock is still there.

Freshness is computed at read time from added_at + shelf life, not stored
as a column that could itself go stale - "now" is the only thing that
actually changes.
"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from pantry_shelf_life import get_shelf_life

# An item is flagged for confirmation once it's lived at least this
# fraction of its typical shelf life, and discarded outright well past it.
# The gap between the two (0.8x-1.5x) is deliberately wide - shelf life is
# a rough estimate, not a hard expiration date, so a narrow band would
# either nag too early or discard too late.
CONFIRM_THRESHOLD = 0.8
DISCARD_THRESHOLD = 1.5


def init_pantry_table(db_path: str = 'recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pantry_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            quantity TEXT,
            added_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual'
        )
    ''')
    conn.commit()
    conn.close()


def _days_since(added_at_iso: str) -> float:
    return (datetime.now() - datetime.fromisoformat(added_at_iso)).total_seconds() / 86400


def _status_for(days_since_added: float, shelf_life_days: int) -> str:
    if shelf_life_days <= 0:
        shelf_life_days = 1
    ratio = days_since_added / shelf_life_days
    if ratio >= DISCARD_THRESHOLD:
        return 'expired'
    if ratio >= CONFIRM_THRESHOLD:
        return 'needs_confirmation'
    return 'fresh'


def discard_expired_items(db_path: str = 'recipes.db') -> int:
    """Removes pantry items far enough past their typical shelf life that
    they're no longer worth even asking about - "intelligently discard old
    knowledge" rather than letting it accumulate forever. Returns the
    number removed."""
    init_pantry_table(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute('SELECT id, name, added_at FROM pantry_items').fetchall()
    expired_ids = []
    for item_id, name, added_at in rows:
        shelf_life = get_shelf_life(name, db_path)
        if _status_for(_days_since(added_at), shelf_life['days']) == 'expired':
            expired_ids.append(item_id)
    if expired_ids:
        conn.executemany('DELETE FROM pantry_items WHERE id = ?', [(i,) for i in expired_ids])
        conn.commit()
    conn.close()
    return len(expired_ids)


def get_items(db_path: str = 'recipes.db') -> List[Dict]:
    """All current pantry items with a computed freshness status
    ('fresh' or 'needs_confirmation' - 'expired' items are swept by
    discard_expired_items() before this returns, so they never appear
    here at all)."""
    discard_expired_items(db_path)
    init_pantry_table(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute('SELECT id, name, quantity, added_at, source FROM pantry_items ORDER BY name ASC').fetchall()
    conn.close()

    items = []
    for item_id, name, quantity, added_at, source in rows:
        shelf_life = get_shelf_life(name, db_path)
        days_since = _days_since(added_at)
        status = _status_for(days_since, shelf_life['days'])
        items.append({
            'id': item_id,
            'name': name,
            'quantity': quantity,
            'added_at': added_at,
            'source': source,
            'days_since_added': round(days_since, 1),
            'shelf_life_days': shelf_life['days'],
            'shelf_life_category': shelf_life['category'],
            'status': status,
        })
    return items


def get_confirmed_fresh_names(db_path: str = 'recipes.db') -> List[str]:
    """Names of pantry items currently trusted as fresh - excludes anything
    flagged 'needs_confirmation', since that's exactly the point: don't let
    /discover (or anything else) assume stock that hasn't been confirmed."""
    return [i['name'] for i in get_items(db_path) if i['status'] == 'fresh']


def add_or_refresh_item(name: str, quantity: Optional[str] = None, source: str = 'manual',
                         db_path: str = 'recipes.db') -> int:
    """Adds a new pantry item, or - if one with the same canonical name
    already exists - refreshes it (added_at = now) instead of creating a
    duplicate. This is what makes checking off a shopping-list item a
    reasonable signal that you've restocked something you already had:
    it doesn't pile up duplicate rows, it just resets the clock."""
    init_pantry_table(db_path)
    name = name.strip().lower()
    conn = sqlite3.connect(db_path)
    existing = conn.execute('SELECT id FROM pantry_items WHERE LOWER(name) = ?', (name,)).fetchone()
    now = datetime.now().isoformat()
    if existing:
        item_id = existing[0]
        conn.execute(
            'UPDATE pantry_items SET added_at = ?, source = ?, quantity = COALESCE(?, quantity) WHERE id = ?',
            (now, source, quantity, item_id),
        )
    else:
        cursor = conn.execute(
            'INSERT INTO pantry_items (name, quantity, added_at, source) VALUES (?, ?, ?, ?)',
            (name, quantity, now, source),
        )
        item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id


def confirm_item(item_id: int, db_path: str = 'recipes.db') -> bool:
    """User confirmed they still have this - resets the shelf-life clock
    rather than leaving added_at at its original (now-stale) value."""
    init_pantry_table(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute('UPDATE pantry_items SET added_at = ? WHERE id = ?', (datetime.now().isoformat(), item_id))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def remove_item(item_id: int, db_path: str = 'recipes.db') -> bool:
    """User confirmed they do NOT have this (or a plain manual removal)."""
    init_pantry_table(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute('DELETE FROM pantry_items WHERE id = ?', (item_id,))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed
