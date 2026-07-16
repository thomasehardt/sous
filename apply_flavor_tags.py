#!/usr/bin/env python3
"""
Apply a batch of {ingredient: [flavor, ...]} classifications (produced by
hand, not the Ollama-based tag_ingredient_batch()) to ingredient_flavors /
ingredient_flavor_tagged, using the exact same insert pattern as
flavor_tagging.tag_all_ingredients() so the two paths stay interchangeable.
Validates every flavor value against the taxonomy before writing anything.

Usage: python3 apply_flavor_tags.py <batch.json>
"""
import json
import sqlite3
import sys

from flavor_taxonomy import FLAVOR_TAXONOMY
from flavor_tagging import init_ingredient_flavors_table

VALID_FLAVORS = frozenset(name for name, _, _ in FLAVOR_TAXONOMY)


def main():
    path = sys.argv[1]
    with open(path) as f:
        tags = json.load(f)

    bad = [(ing, f) for ing, flavors in tags.items() for f in flavors if f not in VALID_FLAVORS]
    if bad:
        print(f"REFUSING to apply: {len(bad)} invalid flavor category usages")
        for b in bad[:10]:
            print(' ', b)
        sys.exit(1)

    init_ingredient_flavors_table('recipes.db')
    conn = sqlite3.connect('recipes.db')
    cursor = conn.cursor()
    tag_rows = 0
    for ingredient, flavors in tags.items():
        for flavor in flavors:
            cursor.execute(
                'INSERT OR IGNORE INTO ingredient_flavors (ingredient, flavor) VALUES (?, ?)',
                (ingredient, flavor),
            )
            tag_rows += 1
        cursor.execute(
            'INSERT OR IGNORE INTO ingredient_flavor_tagged (ingredient) VALUES (?)',
            (ingredient,),
        )
    conn.commit()
    print(f"{len(tags)} ingredients marked tagged, {tag_rows} flavor-tag rows inserted")
    conn.close()


if __name__ == '__main__':
    main()
