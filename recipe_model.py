#!/usr/bin/env python3
"""
Recipe data model for Sous application.
"""

import hashlib
import re
import sqlite3
import json
from datetime import date, datetime
from typing import List, Dict, Optional

from recipe_scaling import parse_ingredient, is_ingredient_section_header

class Recipe:
    """Represents a single recipe."""
    
    def __init__(self, id: Optional[int] = None, title: str = "",
                 description: str = "", ingredients: List[str] = None,
                 instructions: List[str] = None, prep_time: int = 0,
                 cook_time: int = 0, total_time: int = 0, servings: int = 1,
                 cuisine: str = "", difficulty: str = "",
                 url: str = "", created_at: str = "", updated_at: str = "",
                 license: str = "", image_url: str = "", nutrition: str = ""):
        self.id = id
        self.title = title
        self.description = description
        self.ingredients = ingredients or []
        self.instructions = instructions or []
        self.prep_time = prep_time  # in minutes
        self.cook_time = cook_time  # in minutes
        self.total_time = total_time  # in minutes
        self.servings = servings
        self.cuisine = cuisine
        self.difficulty = difficulty  # easy, medium, hard
        self.url = url
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()
        self.license = license  # e.g. 'MIT', 'CC-BY-NC-4.0', 'user-imported'
        self.image_url = image_url
        self.nutrition = nutrition  # short human-readable macro summary, not a full structured blob
    
    def to_dict(self) -> Dict:
        """Convert recipe to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'ingredients': self.ingredients,
            'instructions': self.instructions,
            'prep_time': self.prep_time,
            'cook_time': self.cook_time,
            'total_time': self.total_time,
            'servings': self.servings,
            'cuisine': self.cuisine,
            'difficulty': self.difficulty,
            'url': self.url,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'license': self.license,
            'image_url': self.image_url,
            'nutrition': self.nutrition
        }
    
    @classmethod
    def from_dict(cls, data: Dict):
        """Create recipe from dictionary."""
        return cls(
            id=data.get('id'),
            title=data.get('title', ''),
            description=data.get('description', ''),
            ingredients=data.get('ingredients', []),
            instructions=data.get('instructions', []),
            prep_time=data.get('prep_time', 0),
            cook_time=data.get('cook_time', 0),
            total_time=data.get('total_time', 0),
            servings=data.get('servings', 1),
            cuisine=data.get('cuisine', ''),
            difficulty=data.get('difficulty', ''),
            url=data.get('url', ''),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at'),
            license=data.get('license', ''),
            image_url=data.get('image_url', ''),
            nutrition=data.get('nutrition', '')
        )
    
    def __str__(self):
        return f"Recipe('{self.title}', '{self.cuisine}', {self.servings} servings)"

class RecipeDatabase:
    """Handles database operations for recipes."""
    
    def __init__(self, db_path: str = "recipes.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # WAL mode: readers no longer block on a concurrent writer (the
        # default rollback-journal mode takes an exclusive lock for the
        # whole write). Only matters now that server.py's HTTP server is
        # threaded (see ThreadingRecipeServer) - genuinely concurrent
        # requests were structurally impossible before that, so this was
        # never a real risk until now. A one-time, idempotent, persisted
        # setting (stored in the db file itself, not per-connection).
        cursor.execute('PRAGMA journal_mode=WAL')

        # Create recipes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                ingredients TEXT,
                instructions TEXT,
                prep_time INTEGER,
                cook_time INTEGER,
                total_time INTEGER,
                servings INTEGER,
                cuisine TEXT,
                difficulty TEXT,
                url TEXT,
                created_at TEXT,
                updated_at TEXT,
                license TEXT DEFAULT ''
            )
        ''')

        existing_columns = {row[1] for row in cursor.execute('PRAGMA table_info(recipes)')}
        if 'image_url' not in existing_columns:
            cursor.execute("ALTER TABLE recipes ADD COLUMN image_url TEXT DEFAULT ''")
        if 'nutrition' not in existing_columns:
            cursor.execute("ALTER TABLE recipes ADD COLUMN nutrition TEXT DEFAULT ''")
        if 'completeness_score' not in existing_columns:
            # 0-100 data-quality summary, computed and persisted by
            # compute_completeness_scores.py (not on every request) - NULL
            # until that's been run, not 0, so "never scored" is
            # distinguishable from "scored as genuinely empty".
            cursor.execute("ALTER TABLE recipes ADD COLUMN completeness_score REAL DEFAULT NULL")

        # Full-text search index (title/description/ingredients), used by
        # search_recipes() instead of a LIKE '%query%' scan - ranked by
        # SQLite's bm25(), not row-scan order. A plain (non-contentless)
        # FTS5 table, so the indexed text is duplicated on disk rather than
        # referencing the recipes table - simplest correct option, and an
        # acceptable tradeoff at this data size (~55k rows). rowid is kept
        # equal to recipes.id explicitly so it can be joined straight back.
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
                title, description, ingredients, tokenize='porter unicode61'
            )
        ''')
        fts_count = cursor.execute('SELECT COUNT(*) FROM recipes_fts').fetchone()[0]
        recipes_count = cursor.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]
        if fts_count == 0 and recipes_count > 0:
            cursor.execute('''
                INSERT INTO recipes_fts (rowid, title, description, ingredients)
                SELECT id, title, description, ingredients FROM recipes
            ''')

        # Structured ingredient model: quantity/unit/name parsed out of each
        # free-text ingredient line and persisted, instead of recipe_scaling.py
        # re-parsing raw text on every scale request. Same underlying heuristic
        # parse (there's no structured ingredient data in the source datasets
        # to parse *from* instead) - the difference is it's computed once and
        # queryable, not re-derived live every time. rowid-per-ingredient, not
        # tied to recipes.id, so each recipe has one row per ingredient line.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                quantity REAL,
                unit TEXT,
                name TEXT,
                confidence REAL,
                is_section_header INTEGER NOT NULL DEFAULT 0,
                UNIQUE(recipe_id, position)
            )
        ''')
        existing_ri_columns = {row[1] for row in cursor.execute('PRAGMA table_info(recipe_ingredients)')}
        if 'confidence' not in existing_ri_columns:
            # Parser's own per-field confidence (see recipe_scaling.parse_ingredient),
            # None for rows written before this column existed until
            # reparse_ingredients_nlp.py is re-run.
            cursor.execute("ALTER TABLE recipe_ingredients ADD COLUMN confidence REAL")
        if 'preparation' not in existing_ri_columns:
            # The ML parser's own labeled preparation span ("diced", "finely
            # chopped"), split out from name rather than folded into it -
            # additive, name's existing meaning is unchanged. None for rows
            # written before this column existed until
            # reparse_ingredients_nlp.py is re-run.
            cursor.execute("ALTER TABLE recipe_ingredients ADD COLUMN preparation TEXT")
        if 'is_section_header' not in existing_ri_columns:
            # Component-section labels some source recipes embed as plain
            # ingredient-list entries ("For the Crust:") rather than real
            # ingredients - see is_ingredient_section_header() in
            # recipe_scaling.py. 0 for rows written before this column
            # existed until backfill_section_headers.py is re-run.
            cursor.execute("ALTER TABLE recipe_ingredients ADD COLUMN is_section_header INTEGER NOT NULL DEFAULT 0")
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe ON recipe_ingredients(recipe_id)
        ''')
        ingredients_count = cursor.execute('SELECT COUNT(*) FROM recipe_ingredients').fetchone()[0]
        if ingredients_count == 0 and recipes_count > 0:
            for recipe_id, ingredients_json in cursor.execute('SELECT id, ingredients FROM recipes').fetchall():
                try:
                    ingredient_lines = json.loads(ingredients_json) if ingredients_json else []
                except (json.JSONDecodeError, TypeError):
                    continue
                for position, raw_text in enumerate(ingredient_lines):
                    if is_ingredient_section_header(raw_text):
                        cursor.execute(
                            'INSERT INTO recipe_ingredients (recipe_id, position, raw_text, is_section_header) VALUES (?, ?, ?, 1)',
                            (recipe_id, position, raw_text),
                        )
                        continue
                    parsed = parse_ingredient(raw_text)
                    cursor.execute(
                        'INSERT INTO recipe_ingredients (recipe_id, position, raw_text, quantity, unit, name, confidence, preparation) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        (recipe_id, position, parsed['raw_text'], parsed['quantity'], parsed['unit'], parsed['name'], parsed['confidence'], parsed['preparation']),
                    )

        conn.commit()
        conn.close()

    @staticmethod
    def _build_fts_match(query: str) -> Optional[str]:
        """Turn free-text user input into a safe FTS5 MATCH expression:
        each whitespace-separated token is stripped down to word
        characters (so FTS5 query-syntax metacharacters like " * : ( ) -
        in user input can't be interpreted as query syntax) and turned
        into a prefix match, and tokens are implicitly ANDed by FTS5's
        default query syntax - e.g. `chicken sal` becomes `"chicken"*
        "sal"*`, matching recipes containing a word starting with
        "chicken" and a word starting with "sal". Returns None if nothing
        usable is left (e.g. a query that's only punctuation), so the
        caller can fall back rather than pass FTS5 an empty/invalid query.
        """
        tokens = re.findall(r'\w+', query, flags=re.UNICODE)
        if not tokens:
            return None
        return ' '.join(f'"{token}"*' for token in tokens)

    def _sync_fts(self, cursor, recipe: Recipe) -> None:
        """Keep recipes_fts in sync with a just-saved recipe (delete then
        re-insert - FTS5 has no UPDATE-by-column, and delete-then-insert
        is correct for both the insert and update cases)."""
        cursor.execute('DELETE FROM recipes_fts WHERE rowid=?', (recipe.id,))
        cursor.execute(
            'INSERT INTO recipes_fts (rowid, title, description, ingredients) VALUES (?, ?, ?, ?)',
            (recipe.id, recipe.title, recipe.description, json.dumps(recipe.ingredients)),
        )

    def _sync_structured_ingredients(self, cursor, recipe: Recipe) -> None:
        """Keep recipe_ingredients in sync with a just-saved recipe (delete
        then re-insert, same rationale as _sync_fts - simplest correct
        option for both the insert and update/re-edit cases). Section-header
        lines (see is_ingredient_section_header()) skip the ML parse
        entirely - it's not an ingredient, running parse_ingredient() on it
        only produces inconsistent garbage in quantity/name."""
        cursor.execute('DELETE FROM recipe_ingredients WHERE recipe_id=?', (recipe.id,))
        for position, raw_text in enumerate(recipe.ingredients):
            if is_ingredient_section_header(raw_text):
                cursor.execute(
                    'INSERT INTO recipe_ingredients (recipe_id, position, raw_text, is_section_header) VALUES (?, ?, ?, 1)',
                    (recipe.id, position, raw_text),
                )
                continue
            parsed = parse_ingredient(raw_text)
            cursor.execute(
                'INSERT INTO recipe_ingredients (recipe_id, position, raw_text, quantity, unit, name, confidence, preparation) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (recipe.id, position, parsed['raw_text'], parsed['quantity'], parsed['unit'], parsed['name'], parsed['confidence'], parsed['preparation']),
            )

    def get_structured_ingredients(self, recipe_id: int) -> List[Dict]:
        """Structured (quantity, unit, name, confidence, preparation,
        is_section_header) rows for a recipe's ingredients, in their
        original order."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            'SELECT raw_text, quantity, unit, name, confidence, preparation, is_section_header '
            'FROM recipe_ingredients WHERE recipe_id=? ORDER BY position',
            (recipe_id,),
        ).fetchall()
        conn.close()
        return [
            {'raw_text': r[0], 'quantity': r[1], 'unit': r[2], 'name': r[3], 'confidence': r[4],
             'preparation': r[5], 'is_section_header': bool(r[6])}
            for r in rows
        ]
    
    def save_recipe(self, recipe: Recipe) -> int:
        """Save a recipe to the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Convert lists to JSON strings for storage
        ingredients_json = json.dumps(recipe.ingredients)
        instructions_json = json.dumps(recipe.instructions)
        
        if recipe.id is None:
            # Insert new recipe
            cursor.execute('''
                INSERT INTO recipes
                (title, description, ingredients, instructions, prep_time, cook_time,
                 total_time, servings, cuisine, difficulty, url, created_at, updated_at, license,
                 image_url, nutrition)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                recipe.title, recipe.description, ingredients_json, instructions_json,
                recipe.prep_time, recipe.cook_time, recipe.total_time, recipe.servings,
                recipe.cuisine, recipe.difficulty, recipe.url, recipe.created_at,
                recipe.updated_at, recipe.license, recipe.image_url, recipe.nutrition
            ))
            recipe.id = cursor.lastrowid
        else:
            # Update existing recipe
            cursor.execute('''
                UPDATE recipes SET
                title=?, description=?, ingredients=?, instructions=?, prep_time=?,
                cook_time=?, total_time=?, servings=?, cuisine=?, difficulty=?, url=?,
                updated_at=?, license=?, image_url=?, nutrition=?
                WHERE id=?
            ''', (
                recipe.title, recipe.description, ingredients_json, instructions_json,
                recipe.prep_time, recipe.cook_time, recipe.total_time, recipe.servings,
                recipe.cuisine, recipe.difficulty, recipe.url, recipe.updated_at,
                recipe.license, recipe.image_url, recipe.nutrition, recipe.id
            ))

        self._sync_fts(cursor, recipe)
        self._sync_structured_ingredients(cursor, recipe)

        conn.commit()
        conn.close()
        return recipe.id
    
    def get_recipe(self, recipe_id: int) -> Optional[Recipe]:
        """Retrieve a recipe by ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM recipes WHERE id=?', (recipe_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return self._row_to_recipe(row)
        return None
    
    def get_all_recipes(self, limit: int = 100, offset: int = 0, exclude_builtin: bool = False) -> List[Recipe]:
        """Retrieve a page of recipes, ordered by id so pages are stable
        across requests (SQLite gives no ordering guarantee without an
        explicit ORDER BY). exclude_builtin restricts to the household's
        own recipes (license='user-imported'), per the hide-built-in-recipes
        preference."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if exclude_builtin:
            cursor.execute(
                "SELECT * FROM recipes WHERE license = 'user-imported' ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            )
        else:
            cursor.execute('SELECT * FROM recipes ORDER BY id LIMIT ? OFFSET ?', (limit, offset))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_recipe(row) for row in rows]

    def count_recipes(self, exclude_builtin: bool = False) -> int:
        """Total recipe count, for pagination controls."""
        conn = sqlite3.connect(self.db_path)
        if exclude_builtin:
            count = conn.execute("SELECT COUNT(*) FROM recipes WHERE license = 'user-imported'").fetchone()[0]
        else:
            count = conn.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]
        conn.close()
        return count

    def get_recipe_of_the_day(self, exclude_builtin: bool = False) -> Optional[Recipe]:
        """One recipe with a real image_url, deterministically picked from
        today's UTC date - the same recipe all day, a different one
        tomorrow. Previously used ORDER BY RANDOM() LIMIT 1, which (despite
        being labeled "today's pick" on the home page) actually changed on
        every single page load, not once a day - real user-reported bug.
        Returns None if no recipe qualifies (e.g. a fresh/empty database,
        or exclude_builtin=True and no household recipe has an image yet)
        - the caller falls back gracefully."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        license_clause = "AND license = 'user-imported'" if exclude_builtin else ""
        count = cursor.execute(
            f"SELECT COUNT(*) FROM recipes WHERE image_url IS NOT NULL AND image_url != '' {license_clause}"
        ).fetchone()[0]
        if count == 0:
            conn.close()
            return None
        seed = int(hashlib.sha256(date.today().isoformat().encode()).hexdigest(), 16)
        offset = seed % count
        row = cursor.execute(f'''
            SELECT * FROM recipes WHERE image_url IS NOT NULL AND image_url != '' {license_clause}
            ORDER BY id LIMIT 1 OFFSET ?
        ''', (offset,)).fetchone()
        conn.close()
        return self._row_to_recipe(row) if row else None

    def search_recipes(self, query: str, limit: int = 50, offset: int = 0, exclude_builtin: bool = False) -> List[Recipe]:
        """Full-text search over title/description/ingredients, ranked by
        SQLite's bm25() relevance score (lower = better match), not row
        scan order. Falls back to the old LIKE '%query%' substring scan if
        the query has no usable word characters (_build_fts_match returns
        None) or FTS5 rejects the constructed MATCH expression.

        Both paths apply the same "actually cookable" filter
        find_recipes_by_ingredients() already used (instructions required) -
        without it, a bare ingredient-name-list recipe with no directions can
        outrank a complete one purely on text relevance, which is exactly
        what surfaced during the 2026-07-14 UX review ("chicken curry" top
        hit had no instructions and no ingredient quantities at all). Ties
        broken by completeness_score, same tiebreak already used in
        find_recipes_by_ingredients()/recipe_flavor_index.py.

        exclude_builtin restricts to the household's own recipes
        (license='user-imported'), per the hide-built-in-recipes preference."""
        builtin_clause_fts = "AND recipes.license = 'user-imported'" if exclude_builtin else ""
        builtin_clause_plain = "AND license = 'user-imported'" if exclude_builtin else ""

        match_expr = self._build_fts_match(query)
        if match_expr:
            try:
                conn = sqlite3.connect(self.db_path)
                rows = conn.execute(f'''
                    SELECT recipes.* FROM recipes
                    JOIN recipes_fts ON recipes.id = recipes_fts.rowid
                    WHERE recipes_fts MATCH ?
                      AND recipes.instructions IS NOT NULL AND recipes.instructions != '[]'
                      {builtin_clause_fts}
                    ORDER BY bm25(recipes_fts), recipes.completeness_score DESC
                    LIMIT ? OFFSET ?
                ''', (match_expr, limit, offset)).fetchall()
                conn.close()
                return [self._row_to_recipe(row) for row in rows]
            except sqlite3.OperationalError:
                pass

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(f'''
            SELECT * FROM recipes
            WHERE (title LIKE ? OR description LIKE ? OR ingredients LIKE ?)
              AND instructions IS NOT NULL AND instructions != '[]'
              {builtin_clause_plain}
            ORDER BY completeness_score DESC, id LIMIT ? OFFSET ?
        ''', (f'%{query}%', f'%{query}%', f'%{query}%', limit, offset)).fetchall()
        conn.close()
        return [self._row_to_recipe(row) for row in rows]

    def count_search_results(self, query: str, exclude_builtin: bool = False) -> int:
        """Total matching-recipe count for a search query, for pagination
        controls. Mirrors search_recipes()'s FTS-with-LIKE-fallback logic,
        including the instructions-required filter."""
        builtin_clause_fts = "AND recipes.license = 'user-imported'" if exclude_builtin else ""
        builtin_clause_plain = "AND license = 'user-imported'" if exclude_builtin else ""

        match_expr = self._build_fts_match(query)
        if match_expr:
            try:
                conn = sqlite3.connect(self.db_path)
                count = conn.execute(f'''
                    SELECT COUNT(*) FROM recipes_fts
                    JOIN recipes ON recipes.id = recipes_fts.rowid
                    WHERE recipes_fts MATCH ?
                      AND recipes.instructions IS NOT NULL AND recipes.instructions != '[]'
                      {builtin_clause_fts}
                ''', (match_expr,)).fetchone()[0]
                conn.close()
                return count
            except sqlite3.OperationalError:
                pass

        conn = sqlite3.connect(self.db_path)
        count = conn.execute(f'''
            SELECT COUNT(*) FROM recipes
            WHERE (title LIKE ? OR description LIKE ? OR ingredients LIKE ?)
              AND instructions IS NOT NULL AND instructions != '[]'
              {builtin_clause_plain}
        ''', (f'%{query}%', f'%{query}%', f'%{query}%')).fetchone()[0]
        conn.close()
        return count

    def find_recipes_by_ingredients(self, ingredient_names: List[str], limit: int = 50, exclude_builtin: bool = False) -> List[Dict]:
        """Recipes using the most of the given ingredients on hand ("I have
        onions, what can I make"), ranked by match count. Matches against
        recipe_ingredients.name (parsed canonical name), not raw
        recipes.ingredients text - the same raw-text-vs-canonical-name
        lesson from suggest_companions()'s rekey-era bug earlier this
        project applies here too. Only considers recipes with instructions
        (same "actually cookable" filter suggest_companions() already
        uses) - browsing "what can I make" shouldn't surface
        ingredients-only rows with nothing to follow.

        Returns [{recipe, matched, missing, match_count}] - matched is
        which of the input ingredients this recipe uses, missing is
        whatever else it calls for, so the caller can show "you'll also
        need: X, Y" without a second per-recipe round trip."""
        normed = [i.strip().lower() for i in ingredient_names if i and i.strip()]
        if not normed:
            return []

        conn = sqlite3.connect(self.db_path)
        placeholders = ','.join('?' for _ in normed)
        builtin_clause = "AND r.license = 'user-imported'" if exclude_builtin else ""
        rows = conn.execute(f'''
            SELECT ri.recipe_id, COUNT(DISTINCT ri.name) as match_count
            FROM recipe_ingredients ri
            JOIN recipes r ON r.id = ri.recipe_id
            WHERE ri.name IN ({placeholders})
              AND r.instructions IS NOT NULL AND r.instructions != '[]'
              {builtin_clause}
            GROUP BY ri.recipe_id
            ORDER BY match_count DESC, r.completeness_score DESC
            LIMIT ?
        ''', normed + [limit]).fetchall()

        results = []
        for recipe_id, match_count in rows:
            recipe = self.get_recipe(recipe_id)
            if not recipe:
                continue
            all_names = [
                row[0] for row in conn.execute(
                    'SELECT DISTINCT name FROM recipe_ingredients WHERE recipe_id=? AND name IS NOT NULL',
                    (recipe_id,),
                )
            ]
            matched = [n for n in all_names if n in normed]
            missing = [n for n in all_names if n not in normed]
            results.append({'recipe': recipe, 'matched': matched, 'missing': missing, 'match_count': match_count})
        conn.close()
        return results

    def delete_recipe(self, recipe_id: int) -> bool:
        """Delete a recipe by ID, cascading to every other table keyed by
        recipe_id (recipe_categories, recipe_notes, cook_log, recipe_steps,
        meal_plan_items) so deletion doesn't leave orphaned rows behind.
        Those tables are owned by other modules (categories.py,
        cooking_log.py, meal_planner.py), but all share this same SQLite
        file - deleted directly here, in the same transaction, rather than
        importing those modules (recipe_model.py is the lowest-level
        module; nothing here should depend on the modules that depend on
        it). A table that doesn't exist yet (e.g. a fresh recipes.db where
        a feature has never been used) is skipped, not an error.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cascade_tables = ['recipe_categories', 'recipe_notes', 'cook_log', 'recipe_steps', 'meal_plan_items', 'recipe_ingredients']
        existing_tables = {
            row[0] for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(
                    ','.join('?' * len(cascade_tables))
                ),
                cascade_tables,
            )
        }
        for table in cascade_tables:
            if table in existing_tables:
                cursor.execute(f'DELETE FROM {table} WHERE recipe_id=?', (recipe_id,))
        # recipes_fts is keyed by rowid (= recipes.id), not a recipe_id column.
        cursor.execute('DELETE FROM recipes_fts WHERE rowid=?', (recipe_id,))

        cursor.execute('DELETE FROM recipes WHERE id=?', (recipe_id,))
        deleted = cursor.rowcount > 0

        conn.commit()
        conn.close()
        return deleted
    
    def _row_to_recipe(self, row) -> Recipe:
        """Convert database row to Recipe object."""
        ingredients = json.loads(row[3]) if row[3] else []
        instructions = json.loads(row[4]) if row[4] else []
        
        recipe = Recipe(
            id=row[0],
            title=row[1],
            description=row[2],
            ingredients=ingredients,
            instructions=instructions,
            prep_time=row[5],
            cook_time=row[6],
            total_time=row[7],
            servings=row[8],
            cuisine=row[9],
            difficulty=row[10],
            url=row[11],
            created_at=row[12],
            updated_at=row[13],
            license=row[14] if len(row) > 14 else '',
            image_url=row[15] if len(row) > 15 else '',
            nutrition=row[16] if len(row) > 16 else ''
        )

        return recipe

# Example usage
if __name__ == "__main__":
    # Create a sample recipe database
    db = RecipeDatabase()
    
    # Test creating and saving a recipe
    sample_recipe = Recipe(
        title="Spaghetti Carbonara",
        description="Classic Italian pasta dish with eggs, cheese, pancetta, and pepper.",
        ingredients=["400g spaghetti", "150g pancetta", "4 large eggs", 
                    "100g Pecorino Romano cheese", "Black pepper"],
        instructions=["Cook spaghetti according to package directions.", 
                     "Fry pancetta until crispy.", 
                     "Mix eggs and cheese in a bowl.", 
                     "Combine hot pasta with pancetta, then add egg mixture.",
                     "Serve immediately with extra cheese and pepper."],
        prep_time=10,
        cook_time=15,
        total_time=25,
        servings=4,
        cuisine="Italian",
        difficulty="Medium"
    )
    
    recipe_id = db.save_recipe(sample_recipe)
    print(f"Saved recipe with ID: {recipe_id}")
    
    # Test retrieving the recipe
    retrieved_recipe = db.get_recipe(recipe_id)
    if retrieved_recipe:
        print(f"Retrieved recipe: {retrieved_recipe}")
        print(f"Title: {retrieved_recipe.title}")
        print(f"Ingredients: {', '.join(retrieved_recipe.ingredients)}")