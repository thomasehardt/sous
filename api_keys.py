import hashlib
import secrets
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict


def init_api_keys_table(db_path: str = 'recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked INTEGER NOT NULL DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


def create_api_key(label: str, db_path: str = 'recipes.db') -> Dict:
    """Generates a new API key, stores only its SHA-256 hash (the raw key is
    never persisted - if lost, it can't be recovered, only revoked and
    reissued), and returns the raw key exactly once."""
    init_api_keys_table(db_path)
    raw_key = 'sous_' + secrets.token_urlsafe(32)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        'INSERT INTO api_keys (key_hash, label, created_at) VALUES (?, ?, ?)',
        (_hash_key(raw_key), label, datetime.now().isoformat()),
    )
    key_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {'id': key_id, 'label': label, 'key': raw_key}


def verify_api_key(raw_key: str, db_path: str = 'recipes.db') -> Optional[Dict]:
    """Returns {'id', 'label'} if raw_key matches a non-revoked key, else
    None. Updates last_used_at on success (best-effort, not load-bearing for
    the auth decision itself)."""
    if not raw_key:
        return None
    init_api_keys_table(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        'SELECT id, label FROM api_keys WHERE key_hash = ? AND revoked = 0',
        (_hash_key(raw_key),),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    key_id, label = row
    conn.execute('UPDATE api_keys SET last_used_at = ? WHERE id = ?', (datetime.now().isoformat(), key_id))
    conn.commit()
    conn.close()
    return {'id': key_id, 'label': label}


def list_api_keys(db_path: str = 'recipes.db') -> List[Dict]:
    init_api_keys_table(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        'SELECT id, label, created_at, last_used_at, revoked FROM api_keys ORDER BY id'
    ).fetchall()
    conn.close()
    return [
        {'id': r[0], 'label': r[1], 'created_at': r[2], 'last_used_at': r[3], 'revoked': bool(r[4])}
        for r in rows
    ]


def revoke_api_key(key_id: int, db_path: str = 'recipes.db') -> bool:
    init_api_keys_table(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute('UPDATE api_keys SET revoked = 1 WHERE id = ?', (key_id,))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed
