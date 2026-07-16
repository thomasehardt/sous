import sqlite3
from datetime import datetime

DB_PATH = "recipes.db"


def init_notes_table(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recipe_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def add_note(recipe_id: int, note_text: str, db_path=DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO recipe_notes (recipe_id, note_text, created_at) VALUES (?, ?, ?)",
        (recipe_id, note_text, datetime.now().isoformat()),
    )
    conn.commit()
    last_id = cursor.lastrowid
    conn.close()
    return last_id


def get_notes(recipe_id: int, db_path=DB_PATH) -> list:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, recipe_id, note_text, created_at FROM recipe_notes WHERE recipe_id = ? ORDER BY created_at DESC",
        (recipe_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "recipe_id": r[1], "note_text": r[2], "created_at": r[3]} for r in rows]


def delete_note(note_id: int, db_path=DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM recipe_notes WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()


def init_cook_log_table(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cook_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            cooked_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def log_cooked(recipe_id: int, cooked_at: str = None, db_path=DB_PATH) -> int:
    if cooked_at is None:
        cooked_at = datetime.now().date().isoformat()
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO cook_log (recipe_id, cooked_at) VALUES (?, ?)",
        (recipe_id, cooked_at),
    )
    conn.commit()
    last_id = cursor.lastrowid
    conn.close()
    return last_id


def get_cook_log(recipe_id: int, db_path=DB_PATH) -> list:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, recipe_id, cooked_at FROM cook_log WHERE recipe_id = ? ORDER BY cooked_at DESC",
        (recipe_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "recipe_id": r[1], "cooked_at": r[2]} for r in rows]


def get_cook_history(db_path=DB_PATH) -> list:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, recipe_id, cooked_at FROM cook_log ORDER BY cooked_at DESC"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "recipe_id": r[1], "cooked_at": r[2]} for r in rows]


def delete_cook_log_entry(entry_id: int, db_path=DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM cook_log WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()