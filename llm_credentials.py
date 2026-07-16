"""Storage for LLM provider API keys entered via the Preferences UI.

Deliberately NOT in preferences.py/recipes.db - recipes.db is git-tracked
in this project, and a stored secret there would leak into git history
the moment anyone commits. This is a separate SQLite file
(llm_credentials.db, gitignored - same treatment as uploads/) so the app
still gets to keep "SQLite as the only datastore" (see ARCHITECTURE.md)
without ever putting a key in the versioned file.

No accounts/auth exist anywhere in Sous - whoever can reach /preferences
already has full control over everything else (edit/delete recipes,
etc.), so storing keys here doesn't introduce a new privilege level.
"""
import sqlite3
from typing import Optional

DB_PATH = 'llm_credentials.db'


def init_credentials_table(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS llm_credentials (
            provider TEXT PRIMARY KEY,
            api_key TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def get_api_key(provider: str, db_path: str = DB_PATH) -> Optional[str]:
    init_credentials_table(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT api_key FROM llm_credentials WHERE provider = ?', (provider,)).fetchone()
    conn.close()
    return row[0] if row else None


def has_api_key(provider: str, db_path: str = DB_PATH) -> bool:
    return bool(get_api_key(provider, db_path))


def save_api_key(provider: str, api_key: str, db_path: str = DB_PATH) -> None:
    init_credentials_table(db_path)
    api_key = (api_key or '').strip()
    conn = sqlite3.connect(db_path)
    if api_key:
        conn.execute('''
            INSERT INTO llm_credentials (provider, api_key) VALUES (?, ?)
            ON CONFLICT(provider) DO UPDATE SET api_key = excluded.api_key
        ''', (provider, api_key))
    else:
        conn.execute('DELETE FROM llm_credentials WHERE provider = ?', (provider,))
    conn.commit()
    conn.close()
