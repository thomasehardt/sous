"""
Multiple photos per recipe. Each row is either an external URL (imported,
or added by hand) or a locally-uploaded file (see uploads.py for the
upload/serving side) - exactly one of `url`/`filename` is set per row.

recipes.image_url stays in sync as a denormalized "first image" cache
(_sync_primary_image()) so every existing thumbnail call site
(recipe_thumb_html, search results, home page, etc.) keeps working
unchanged - this module is additive, not a replacement for that column.
"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional


def init_recipe_images_table(db_path: str = 'recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recipe_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            url TEXT,
            filename TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recipe_images_recipe_id ON recipe_images(recipe_id)')

    # One-time backfill: every recipe that already has a single image_url
    # (from import) but no recipe_images rows yet gets that URL as its
    # first gallery image, so existing images aren't orphaned by this
    # table's introduction. Idempotent - only fires for recipes with zero
    # existing recipe_images rows, safe to re-run.
    rows = conn.execute('''
        SELECT r.id, r.image_url FROM recipes r
        WHERE r.image_url IS NOT NULL AND r.image_url != ''
          AND NOT EXISTS (SELECT 1 FROM recipe_images ri WHERE ri.recipe_id = r.id)
    ''').fetchall()
    now = datetime.now().isoformat()
    for recipe_id, image_url in rows:
        conn.execute(
            'INSERT INTO recipe_images (recipe_id, url, position, created_at) VALUES (?, ?, 0, ?)',
            (recipe_id, image_url, now),
        )
    conn.commit()
    conn.close()


def _resolve_src(url: Optional[str], filename: Optional[str]) -> str:
    return url if url else f'/uploads/{filename}'


def get_images(recipe_id: int, db_path: str = 'recipes.db') -> List[Dict]:
    init_recipe_images_table(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        'SELECT id, url, filename, position FROM recipe_images WHERE recipe_id = ? ORDER BY position ASC, id ASC',
        (recipe_id,),
    ).fetchall()
    conn.close()
    return [{'id': r[0], 'src': _resolve_src(r[1], r[2]), 'position': r[3]} for r in rows]


def _sync_primary_image(conn, recipe_id: int):
    row = conn.execute(
        'SELECT url, filename FROM recipe_images WHERE recipe_id = ? ORDER BY position ASC, id ASC LIMIT 1',
        (recipe_id,),
    ).fetchone()
    new_primary = _resolve_src(row[0], row[1]) if row else ''
    conn.execute('UPDATE recipes SET image_url = ? WHERE id = ?', (new_primary, recipe_id))


def _add_image(recipe_id: int, url: Optional[str], filename: Optional[str], db_path: str) -> int:
    init_recipe_images_table(db_path)
    conn = sqlite3.connect(db_path)
    max_pos = conn.execute(
        'SELECT COALESCE(MAX(position), -1) FROM recipe_images WHERE recipe_id = ?', (recipe_id,)
    ).fetchone()[0]
    cursor = conn.execute(
        'INSERT INTO recipe_images (recipe_id, url, filename, position, created_at) VALUES (?, ?, ?, ?, ?)',
        (recipe_id, url, filename, max_pos + 1, datetime.now().isoformat()),
    )
    image_id = cursor.lastrowid
    _sync_primary_image(conn, recipe_id)
    conn.commit()
    conn.close()
    return image_id


def add_image_url(recipe_id: int, url: str, db_path: str = 'recipes.db') -> int:
    return _add_image(recipe_id, url.strip(), None, db_path)


def add_image_upload(recipe_id: int, filename: str, db_path: str = 'recipes.db') -> int:
    return _add_image(recipe_id, None, filename, db_path)


def remove_image(image_id: int, db_path: str = 'recipes.db') -> Optional[str]:
    """Deletes the row and returns the removed image's local filename (so
    the caller can also delete the file from the uploads directory - this
    module only knows about the DB, not the filesystem), or '' if the
    removed image was URL-based, or None if no such image existed."""
    init_recipe_images_table(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT recipe_id, filename FROM recipe_images WHERE id = ?', (image_id,)).fetchone()
    if row is None:
        conn.close()
        return None
    recipe_id, filename = row
    conn.execute('DELETE FROM recipe_images WHERE id = ?', (image_id,))
    _sync_primary_image(conn, recipe_id)
    conn.commit()
    conn.close()
    return filename or ''
