"""One-time backfill marking existing recipe_ingredients rows that are
actually component-section labels ("For the Crust:", "Filling:") rather
than real ingredients - see is_ingredient_section_header() in
recipe_scaling.py, added alongside this script.

Deterministic regex check, not the ML parser - no per-row model call, so
this is a fast single pass over the whole table (~517K rows) rather than
the tens-of-minutes-scale batch jobs elsewhere in this project's history.
Clears quantity/unit/name/confidence/preparation on matched rows (they
hold garbage from the ML parser having been run on non-ingredient text -
e.g. name="For the Crust" on one row, nothing at all on another for the
same kind of line) since raw_text is the only real signal for a header.

Idempotent - a deterministic re-check of the same raw_text, safe to
re-run. Commits every 5000 rows so a Ctrl-C only loses the current batch.
"""
import sqlite3
import time

from recipe_model import RecipeDatabase
from recipe_scaling import is_ingredient_section_header

BATCH_SIZE = 5000


def main():
    # Ensure the is_section_header column migration has run - this script
    # connects directly with sqlite3 rather than through RecipeDatabase, so
    # it wouldn't otherwise trigger it.
    RecipeDatabase('recipes.db')

    conn = sqlite3.connect('recipes.db')
    rows = conn.execute('SELECT id, raw_text, is_section_header FROM recipe_ingredients ORDER BY id').fetchall()
    total = len(rows)
    print(f"checking {total} recipe_ingredients rows...")

    start = time.time()
    marked = 0
    unmarked = 0
    for i, (row_id, raw_text, currently_marked) in enumerate(rows, 1):
        is_header = is_ingredient_section_header(raw_text)
        if is_header and not currently_marked:
            conn.execute(
                'UPDATE recipe_ingredients SET is_section_header=1, quantity=NULL, unit=NULL, '
                'name=NULL, confidence=NULL, preparation=NULL WHERE id=?',
                (row_id,),
            )
            marked += 1
        elif not is_header and currently_marked:
            # Shouldn't happen (nothing sets is_section_header=1 except this
            # same check), but correct it rather than leave a stale flag.
            conn.execute('UPDATE recipe_ingredients SET is_section_header=0 WHERE id=?', (row_id,))
            unmarked += 1
        if i % BATCH_SIZE == 0:
            conn.commit()
            elapsed = time.time() - start
            print(f"  {i}/{total} ({elapsed:.0f}s elapsed)")

    conn.commit()
    conn.close()
    print(f"done in {time.time()-start:.0f}s: {marked} newly marked as section headers, {unmarked} unexpected stale flags")


if __name__ == '__main__':
    main()
