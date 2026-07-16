import sqlite3
from collections import Counter
from itertools import product
from typing import Dict, List, Tuple


def _flavor_map(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for ingredient, flavor in conn.execute("SELECT ingredient, flavor FROM ingredient_flavors"):
        result.setdefault(ingredient, []).append(flavor)
    return result


def rebuild_flavor_pair_stats(db_path: str = "recipes.db") -> int:
    """Roll up ingredient_pairs into flavor-level pairing stats.

    For every row in ingredient_pairs (ingredient_a, ingredient_b, pair_count),
    look up each ingredient's flavor tags via ingredient_flavors, and for every
    (flavor_a, flavor_b) combination across the two ingredients' flavor sets,
    add that row's pair_count to a running total for that flavor pair. Store
    flavor_a/flavor_b sorted alphabetically (flavor_a <= flavor_b) so each
    unordered flavor pair appears exactly once. Self-pairs (e.g. sweet+sweet,
    meaning two ingredients that are both tagged sweet were used together) are
    valid and must be kept, not skipped.

    Creates table flavor_pair_stats(flavor_a TEXT, flavor_b TEXT, pair_count
    INTEGER, PRIMARY KEY (flavor_a, flavor_b)) if it doesn't exist, clears any
    existing rows first (so this function is safely re-runnable), computes the
    full rollup, inserts the results, commits, and returns the resulting row
    count in flavor_pair_stats.
    """
    conn = sqlite3.connect(db_path)
    try:
        # Create table if it doesn't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flavor_pair_stats (
                flavor_a TEXT,
                flavor_b TEXT,
                pair_count INTEGER,
                PRIMARY KEY (flavor_a, flavor_b)
            )
        """)
        
        # Clear existing rows
        conn.execute("DELETE FROM flavor_pair_stats")
        
        # Get flavor mapping
        flavor_map = _flavor_map(conn)
        
        # Aggregate pair counts by flavor pairs
        flavor_counts: Dict[Tuple[str, str], int] = Counter()
        
        # Process each ingredient pair
        for ingredient_a, ingredient_b, pair_count in conn.execute("SELECT ingredient_a, ingredient_b, pair_count FROM ingredient_pairs"):
            # Get flavors for both ingredients
            flavors_a = flavor_map.get(ingredient_a, [])
            flavors_b = flavor_map.get(ingredient_b, [])
            
            # For each combination of flavors, add to count
            for flavor_a, flavor_b in product(flavors_a, flavors_b):
                # Ensure alphabetical order for the pair
                if flavor_a > flavor_b:
                    flavor_a, flavor_b = flavor_b, flavor_a
                
                flavor_counts[(flavor_a, flavor_b)] += pair_count
        
        # Insert results into table
        insert_data = [(flavor_a, flavor_b, count) for (flavor_a, flavor_b), count in flavor_counts.items()]
        conn.executemany("INSERT INTO flavor_pair_stats VALUES (?, ?, ?)", insert_data)
        
        # Commit and return row count
        conn.commit()
        return len(insert_data)
    
    finally:
        conn.close()


def get_flavor_pair_count(flavor_a: str, flavor_b: str, db_path: str = "recipes.db") -> int:
    """Return the pair_count for this unordered flavor pair (0 if absent)."""
    conn = sqlite3.connect(db_path)
    try:
        # Ensure alphabetical order
        if flavor_a > flavor_b:
            flavor_a, flavor_b = flavor_b, flavor_a
            
        result = conn.execute("SELECT pair_count FROM flavor_pair_stats WHERE flavor_a=? AND flavor_b=?", (flavor_a, flavor_b)).fetchone()
        return result[0] if result else 0
    finally:
        conn.close()


def get_common_flavor_pairs(limit: int = 20, db_path: str = "recipes.db") -> List[Tuple[str, str, int]]:
    """Top `limit` (flavor_a, flavor_b, pair_count) rows from flavor_pair_stats,
    ordered by pair_count descending."""
    conn = sqlite3.connect(db_path)
    try:
        results = conn.execute("SELECT flavor_a, flavor_b, pair_count FROM flavor_pair_stats ORDER BY pair_count DESC LIMIT ?", (limit,)).fetchall()
        return results
    finally:
        conn.close()


def get_rare_flavor_pairs(limit: int = 20, db_path: str = "recipes.db") -> List[Tuple[str, str, int]]:
    """Bottom `limit` (flavor_a, flavor_b, pair_count) rows from
    flavor_pair_stats WHERE pair_count > 0, ordered by pair_count ascending
    (rarest pairs that do actually occur at least once - not zero-count
    pairs, those belong to get_never_paired_flavors)."""
    conn = sqlite3.connect(db_path)
    try:
        results = conn.execute("SELECT flavor_a, flavor_b, pair_count FROM flavor_pair_stats WHERE pair_count > 0 ORDER BY pair_count ASC LIMIT ?", (limit,)).fetchall()
        return results
    finally:
        conn.close()


def get_never_paired_flavors(db_path: str = "recipes.db") -> List[Tuple[str, str]]:
    """All (flavor_a, flavor_b) unordered combinations (including self-pairs)
    across every name in flavor_categories, where NO row exists in
    flavor_pair_stats for that pair (i.e. zero observed co-occurrence -
    genuinely never paired, not just low). Compare against the full set of
    17 flavor_categories.name values, not just flavors present in
    flavor_pair_stats."""
    conn = sqlite3.connect(db_path)
    try:
        # Get all flavor categories
        flavors = [row[0] for row in conn.execute("SELECT name FROM flavor_categories").fetchall()]
        
        # Get existing flavor pairs
        existing_pairs = set()
        for flavor_a, flavor_b in conn.execute("SELECT flavor_a, flavor_b FROM flavor_pair_stats").fetchall():
            if flavor_a > flavor_b:
                flavor_a, flavor_b = flavor_b, flavor_a
            existing_pairs.add((flavor_a, flavor_b))
        
        # Find all combinations that are not in the existing pairs
        never_paired = []
        for flavor_a, flavor_b in product(flavors, repeat=2):
            if flavor_a > flavor_b:
                flavor_a, flavor_b = flavor_b, flavor_a
            if (flavor_a, flavor_b) not in existing_pairs:
                never_paired.append((flavor_a, flavor_b))
        
        return never_paired
    finally:
        conn.close()