import sqlite3
from typing import Dict, List, Optional


def init_recipe_flavors_table(db_path: str = 'recipes.db'):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recipe_flavors (
            recipe_id INTEGER NOT NULL,
            flavor TEXT NOT NULL,
            weight INTEGER NOT NULL,
            PRIMARY KEY (recipe_id, flavor)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_recipe_flavors_flavor ON recipe_flavors(flavor)')
    conn.commit()
    conn.close()


def build_recipe_flavor_index(db_path: str = 'recipes.db') -> int:
    """Precompute per-recipe flavor weights from recipe_ingredients + ingredient_flavors so
    intent search can filter/rank by flavor with a plain indexed SQL query instead of
    re-deriving each recipe's profile (a Python-side join over its ingredients) per request.
    Safe to re-run - fully replaces prior contents."""
    init_recipe_flavors_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute('DELETE FROM recipe_flavors')
    conn.execute('''
        INSERT INTO recipe_flavors (recipe_id, flavor, weight)
        SELECT ri.recipe_id, f.flavor, COUNT(*)
        FROM recipe_ingredients ri
        JOIN ingredient_flavors f ON f.ingredient = ri.name
        GROUP BY ri.recipe_id, f.flavor
    ''')
    count = conn.execute('SELECT COUNT(*) FROM recipe_flavors').fetchone()[0]
    conn.commit()
    conn.close()
    return count


def find_recipes_by_flavors(
    flavors: List[str],
    db_path: str = 'recipes.db',
    limit: int = 30,
    cuisine: Optional[str] = None,
    max_total_time: Optional[int] = None,
) -> List[Dict]:
    """Rank recipes by how many of the requested flavors they match (weighted by ingredient
    count contributing each flavor), optionally narrowed by cuisine/max total time."""
    normed_flavors = [f.strip().lower() for f in flavors if f and f.strip()]
    if not normed_flavors:
        return []

    conn = sqlite3.connect(db_path)
    placeholders = ','.join('?' for _ in normed_flavors)
    where = [f'rf.flavor IN ({placeholders})']
    params: List = list(normed_flavors)

    if cuisine:
        where.append('r.cuisine LIKE ?')
        params.append(f'%{cuisine.strip().lower()}%')
    if max_total_time:
        where.append('(r.total_time IS NOT NULL AND r.total_time <= ?)')
        params.append(max_total_time)
    where.append("r.instructions IS NOT NULL AND r.instructions != '[]'")

    sql = f'''
        SELECT rf.recipe_id, COUNT(DISTINCT rf.flavor) as flavor_match_count, SUM(rf.weight) as total_weight
        FROM recipe_flavors rf
        JOIN recipes r ON r.id = rf.recipe_id
        WHERE {' AND '.join(where)}
        GROUP BY rf.recipe_id
        ORDER BY flavor_match_count DESC, total_weight DESC, r.completeness_score DESC
        LIMIT ?
    '''
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {'recipe_id': recipe_id, 'flavor_match_count': flavor_match_count, 'total_weight': total_weight}
        for recipe_id, flavor_match_count, total_weight in rows
    ]


if __name__ == '__main__':
    n = build_recipe_flavor_index()
    print(f'Indexed {n} (recipe_id, flavor) rows')
