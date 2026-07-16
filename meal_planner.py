#!/usr/bin/env python3
"""
Meal planning for Sous: combining recipes into plans, suggesting companion
dishes via ingredient co-occurrence, and backward-scheduling cooking steps
from a target eat time.

Honesty note on scheduling: recipes only have free-text instructions, no
structured timing data. Step durations and active/passive classification
below are heuristic estimates (regex + keyword matching), not true recipe
understanding. They're a genuinely useful approximation, not a guarantee.
"""

import sqlite3
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import numpy as np

from recipe_model import Recipe, RecipeDatabase
from embeddings import cosine_similarity
from flavor_queries import get_recipe_flavor_profile

DEFAULT_STEP_MINUTES = 5  # fallback when a step mentions no explicit duration

# Keywords that mean "this step runs unattended once started" - the cook is
# free to do other active work while it happens.
PASSIVE_KEYWORDS = [
    'bake', 'roast', 'simmer', 'marinate', 'chill', 'refrigerate', 'freeze',
    'rest', 'rise', 'proof', 'cool', 'soak', 'steep', 'braise', 'slow cook',
    'let stand', 'let sit', 'set aside', 'preheat',
]

_DURATION_PATTERN = re.compile(
    r'(\d+)(?:\s*(?:to|-)\s*\d+)?\s*(hour|hr|minute|min)s?\b',
    re.IGNORECASE
)


def extract_step_duration(text: str) -> int:
    """Extract an estimated duration in minutes from a single instruction
    step's text. Uses the first explicit time mention found (e.g. "bake for
    25 minutes", "1 to 2 hours"), with "overnight" treated as ~8 hours since
    it's a very common phrase with no numeric duration otherwise. Falls back
    to DEFAULT_STEP_MINUTES when nothing matches - a real limitation, not a
    precise estimate."""
    if not text:
        return DEFAULT_STEP_MINUTES
    if 'overnight' in text.lower():
        return 8 * 60
    match = _DURATION_PATTERN.search(text)
    if not match:
        return DEFAULT_STEP_MINUTES
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith('hour') or unit == 'hr':
        return amount * 60
    return amount


def classify_step_type(text: str) -> str:
    """Classify a step as 'passive' (unattended once started - baking,
    marinating, chilling) or 'active' (requires the cook's attention).
    Heuristic keyword match, defaults to 'active' when uncertain."""
    if not text:
        return 'active'
    lowered = text.lower()
    for keyword in PASSIVE_KEYWORDS:
        if keyword in lowered:
            return 'passive'
    return 'active'


class MealPlanDatabase:
    """Handles meal plans, meal plan items, cached per-recipe step
    breakdowns, and ingredient co-occurrence pairing - all in the same
    SQLite file as the recipes table."""

    def __init__(self, db_path: str = "recipes.db"):
        self.db_path = db_path
        self.init_database()
        self._embedding_matrix_cache = None  # (ingredient_list, np.ndarray), lazy-built - see top_embedding_similar_ingredients

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meal_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target_eat_time TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meal_plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meal_plan_id INTEGER NOT NULL,
                recipe_id INTEGER NOT NULL,
                position INTEGER DEFAULT 0,
                FOREIGN KEY (meal_plan_id) REFERENCES meal_plans(id),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recipe_steps (
                recipe_id INTEGER NOT NULL,
                step_index INTEGER NOT NULL,
                text TEXT,
                duration_minutes INTEGER,
                step_type TEXT,
                PRIMARY KEY (recipe_id, step_index)
            )
        ''')

        # Structured step model: previously only populated lazily (on first
        # backward-schedule request for a given recipe, via get_recipe_steps()
        # below). Backfilled eagerly here instead, for every recipe that has
        # instructions, so it's a real up-front structured dataset rather than
        # a cache that happens to fill in over time. Same underlying heuristic
        # duration/active-passive extraction either way (see module docstring)
        # - get_recipe_steps() is unchanged and still self-heals (recomputes)
        # if a recipe's instructions ever change after this backfill runs.
        #
        # Checked per-recipe (NOT IN, below) rather than "is the table empty" -
        # get_recipe_steps() may have already lazily cached a handful of
        # recipes in a prior session, and an empty-table check would wrongly
        # treat that partial cache as "already fully backfilled" and skip
        # every other recipe.
        rows_to_backfill = cursor.execute('''
            SELECT id, instructions FROM recipes
            WHERE instructions IS NOT NULL AND instructions != '[]'
              AND id NOT IN (SELECT DISTINCT recipe_id FROM recipe_steps)
        ''').fetchall()
        for recipe_id, instructions_json in rows_to_backfill:
            try:
                instructions = json.loads(instructions_json) if instructions_json else []
            except (json.JSONDecodeError, TypeError):
                continue
            for i, step_text in enumerate(instructions):
                cursor.execute(
                    'INSERT INTO recipe_steps (recipe_id, step_index, text, duration_minutes, step_type) VALUES (?, ?, ?, ?, ?)',
                    (recipe_id, i, step_text, extract_step_duration(step_text), classify_step_type(step_text)),
                )

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ingredient_pairs (
                ingredient_a TEXT NOT NULL,
                ingredient_b TEXT NOT NULL,
                pair_count INTEGER NOT NULL,
                PRIMARY KEY (ingredient_a, ingredient_b)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ingredient_totals (
                ingredient TEXT PRIMARY KEY,
                total_count INTEGER NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ingredient_pairs_a ON ingredient_pairs(ingredient_a)
        ''')

        conn.commit()
        conn.close()

    # ---- Meal plan CRUD ----

    def create_plan(self, name: str, target_eat_time: str = "") -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            'INSERT INTO meal_plans (name, target_eat_time, created_at, updated_at) VALUES (?, ?, ?, ?)',
            (name, target_eat_time, now, now)
        )
        plan_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return plan_id

    def get_plan(self, plan_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id, name, target_eat_time, created_at, updated_at FROM meal_plans WHERE id=?', (plan_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {'id': row[0], 'name': row[1], 'target_eat_time': row[2], 'created_at': row[3], 'updated_at': row[4]}

    def list_plans(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id, name, target_eat_time, created_at, updated_at FROM meal_plans ORDER BY id DESC')
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'target_eat_time': r[2], 'created_at': r[3], 'updated_at': r[4]} for r in rows]

    def delete_plan(self, plan_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM meal_plan_items WHERE meal_plan_id=?', (plan_id,))
        cursor.execute('DELETE FROM meal_plans WHERE id=?', (plan_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def add_recipe_to_plan(self, plan_id: int, recipe_id: int) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COALESCE(MAX(position), -1) FROM meal_plan_items WHERE meal_plan_id=?', (plan_id,))
        next_position = cursor.fetchone()[0] + 1
        cursor.execute(
            'INSERT INTO meal_plan_items (meal_plan_id, recipe_id, position) VALUES (?, ?, ?)',
            (plan_id, recipe_id, next_position)
        )
        item_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return item_id

    def remove_recipe_from_plan(self, plan_id: int, recipe_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM meal_plan_items WHERE meal_plan_id=? AND recipe_id=?', (plan_id, recipe_id))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def get_plan_recipe_ids(self, plan_id: int) -> List[int]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT recipe_id FROM meal_plan_items WHERE meal_plan_id=? ORDER BY position', (plan_id,))
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]

    # ---- Recipe step cache ----

    def get_recipe_steps(self, recipe: Recipe) -> List[Dict]:
        """Return cached step breakdown for a recipe, computing and caching
        it on first access. Returns [] for recipes with no instructions
        (e.g. the CC-BY-NC ingredients-only batch)."""
        if not recipe.instructions:
            return []

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT step_index, text, duration_minutes, step_type FROM recipe_steps WHERE recipe_id=? ORDER BY step_index',
            (recipe.id,)
        )
        rows = cursor.fetchall()

        if rows and len(rows) == len(recipe.instructions):
            conn.close()
            return [{'index': r[0], 'text': r[1], 'duration_minutes': r[2], 'step_type': r[3]} for r in rows]

        # Not cached, or stale (instruction count changed) - (re)build.
        cursor.execute('DELETE FROM recipe_steps WHERE recipe_id=?', (recipe.id,))
        steps = []
        for i, step_text in enumerate(recipe.instructions):
            duration = extract_step_duration(step_text)
            step_type = classify_step_type(step_text)
            cursor.execute(
                'INSERT INTO recipe_steps (recipe_id, step_index, text, duration_minutes, step_type) VALUES (?, ?, ?, ?, ?)',
                (recipe.id, i, step_text, duration, step_type)
            )
            steps.append({'index': i, 'text': step_text, 'duration_minutes': duration, 'step_type': step_type})
        conn.commit()
        conn.close()
        return steps

    # ---- Backward scheduling ----

    def backward_schedule_recipe(self, recipe: Recipe, eat_time: datetime) -> List[Dict]:
        """Given a recipe and a target eat (serve) time, compute when each
        step should start, working backward from the end. Returns a list of
        dicts with start_time, end_time, text, duration_minutes, step_type,
        ordered by start_time."""
        steps = self.get_recipe_steps(recipe)
        if not steps:
            return []

        total_minutes = sum(s['duration_minutes'] for s in steps)
        cursor_time = eat_time - timedelta(minutes=total_minutes)

        schedule = []
        for step in steps:
            start = cursor_time
            end = start + timedelta(minutes=step['duration_minutes'])
            schedule.append({
                'recipe_id': recipe.id,
                'recipe_title': recipe.title,
                'start_time': start,
                'end_time': end,
                'text': step['text'],
                'duration_minutes': step['duration_minutes'],
                'step_type': step['step_type'],
            })
            cursor_time = end
        return schedule

    def backward_schedule_plan(self, plan_id: int, recipe_db: RecipeDatabase, eat_time: datetime) -> Dict:
        """Backward-schedule every recipe in a plan against the same eat
        time, merge into one timeline sorted by start time, and flag active
        steps from different recipes that overlap - the cook can't do two
        active tasks at once. This does NOT auto-resolve conflicts (that's
        a harder scheduling-optimization problem); it surfaces them so the
        cook can sequence or multitask themselves."""
        recipe_ids = self.get_plan_recipe_ids(plan_id)
        all_steps = []
        skipped_no_instructions = []
        for recipe_id in recipe_ids:
            recipe = recipe_db.get_recipe(recipe_id)
            if not recipe:
                continue
            if not recipe.instructions:
                skipped_no_instructions.append(recipe.title)
                continue
            all_steps.extend(self.backward_schedule_recipe(recipe, eat_time))

        all_steps.sort(key=lambda s: s['start_time'])

        conflicts = []
        active_steps = [s for s in all_steps if s['step_type'] == 'active']
        for i in range(len(active_steps)):
            for j in range(i + 1, len(active_steps)):
                a, b = active_steps[i], active_steps[j]
                if a['recipe_id'] == b['recipe_id']:
                    continue
                if a['start_time'] < b['end_time'] and b['start_time'] < a['end_time']:
                    conflicts.append({
                        'a': f"{a['recipe_title']}: {a['text']}",
                        'b': f"{b['recipe_title']}: {b['text']}",
                        'overlap_start': max(a['start_time'], b['start_time']),
                        'overlap_end': min(a['end_time'], b['end_time']),
                    })

        return {
            'eat_time': eat_time,
            'timeline': all_steps,
            'conflicts': conflicts,
            'skipped_no_instructions': skipped_no_instructions,
        }

    # ---- Ingredient co-occurrence pairing ----

    def rebuild_ingredient_pairs(self, recipe_db: RecipeDatabase, limit: Optional[int] = None) -> int:
        """Recompute the ingredient co-occurrence table from every recipe's
        parsed ingredient names. Returns the number of distinct pairs
        stored. Safe to re-run any time (fully replaces prior data).

        Keyed off recipe_ingredients.name (the parser's cleaned ingredient
        name), not raw ingredient text - as of 2026-07-12, previously kept
        off recipes.ingredients directly, which meant "onion", "onion,
        diced" and "1/2 onion" were three different co-occurrence keys.
        Rows with no parsed name (garnish notes like "for serving") are
        skipped, not counted as an ingredient."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        query = (
            'SELECT recipe_id, name FROM recipe_ingredients '
            'WHERE name IS NOT NULL AND TRIM(name) != "" '
            'ORDER BY recipe_id'
        )
        if limit:
            query = (
                'SELECT recipe_id, name FROM recipe_ingredients WHERE recipe_id IN '
                f'(SELECT id FROM recipes LIMIT {int(limit)}) '
                'AND name IS NOT NULL AND TRIM(name) != "" ORDER BY recipe_id'
            )
        cursor.execute(query)
        rows = cursor.fetchall()

        pair_counts: Dict[tuple, int] = {}
        ingredient_totals: Dict[str, int] = {}
        current_recipe_id = None
        current_names: set = set()

        def flush():
            normed = sorted(current_names)
            for ing in normed:
                ingredient_totals[ing] = ingredient_totals.get(ing, 0) + 1
            for i in range(len(normed)):
                for j in range(i + 1, len(normed)):
                    pair = (normed[i], normed[j])
                    pair_counts[pair] = pair_counts.get(pair, 0) + 1

        for recipe_id, name in rows:
            if recipe_id != current_recipe_id:
                if current_recipe_id is not None:
                    flush()
                current_recipe_id = recipe_id
                current_names = set()
            current_names.add(name.strip().lower())
        if current_recipe_id is not None:
            flush()

        cursor.execute('DELETE FROM ingredient_pairs')
        cursor.executemany(
            'INSERT INTO ingredient_pairs (ingredient_a, ingredient_b, pair_count) VALUES (?, ?, ?)',
            [(a, b, count) for (a, b), count in pair_counts.items()]
        )
        cursor.execute('DELETE FROM ingredient_totals')
        cursor.executemany(
            'INSERT INTO ingredient_totals (ingredient, total_count) VALUES (?, ?)',
            list(ingredient_totals.items())
        )
        conn.commit()
        conn.close()
        return len(pair_counts)

    def get_ingredient_total(self, ingredient: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT total_count FROM ingredient_totals WHERE ingredient=?', (ingredient.strip().lower(),))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else 0

    def top_pairs_for_ingredient(self, ingredient: str, limit: int = 10) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        ingredient = ingredient.strip().lower()
        cursor.execute('''
            SELECT ingredient_a, ingredient_b, pair_count FROM ingredient_pairs
            WHERE ingredient_a = ? OR ingredient_b = ?
            ORDER BY pair_count DESC LIMIT ?
        ''', (ingredient, ingredient, limit))
        rows = cursor.fetchall()
        conn.close()
        results = []
        for a, b, count in rows:
            other = b if a == ingredient else a
            results.append({'ingredient': other, 'count': count})
        return results

    def get_ingredient_embedding_similarity(self, ingredient_a: str, ingredient_b: str) -> float:
        """Semantic similarity between two ingredients via their cached
        Ollama embeddings (see embeddings.py). Returns 0.0 if either
        ingredient has no stored embedding (e.g. below the total_count
        threshold used when the embeddings table was built)."""
        ingredient_a = ingredient_a.strip().lower()
        ingredient_b = ingredient_b.strip().lower()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT vector FROM ingredient_embeddings WHERE ingredient=?', (ingredient_a,))
        row_a = cursor.fetchone()
        cursor.execute('SELECT vector FROM ingredient_embeddings WHERE ingredient=?', (ingredient_b,))
        row_b = cursor.fetchone()
        conn.close()

        if not row_a or not row_b:
            return 0.0

        vector_a = json.loads(row_a[0])
        vector_b = json.loads(row_b[0])
        return cosine_similarity(vector_a, vector_b)

    def _get_embedding_matrix(self):
        """Lazily build and cache (ingredient_list, index_by_ingredient,
        row-normalized matrix) for every stored embedding, reused for the
        lifetime of this MealPlanDatabase instance rather than rebuilt
        per call. json.loads() on ~7,200 stored vectors takes ~2s - that
        cost belongs once per process, not once per HTTP request. Rows
        are pre-normalized so a later cosine similarity is just a plain
        dot product, not a repeated norm computation."""
        if self._embedding_matrix_cache is None:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute('SELECT ingredient, vector FROM ingredient_embeddings').fetchall()
            conn.close()
            ingredients = [r[0] for r in rows]
            matrix = np.array([json.loads(r[1]) for r in rows])
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            normalized = matrix / (norms + 1e-10)
            index = {ing: i for i, ing in enumerate(ingredients)}
            self._embedding_matrix_cache = (ingredients, index, normalized)
        return self._embedding_matrix_cache

    def top_embedding_similar_ingredients(self, ingredient: str, limit: int = 10) -> List[Dict]:
        """Ingredients most semantically similar to the given one by cached
        embedding (see embeddings.py) - e.g. surfaces "lime" for "lemon"
        even though they may never literally co-occur in a recipe, which
        top_pairs_for_ingredient() (literal co-occurrence) can't see.
        Returns [] if it has no stored embedding (below the total_count
        threshold used when the embeddings table was built - see
        embeddings.build_ingredient_embeddings).

        Excludes not just the ingredient itself but any candidate that's a
        substring match either direction (e.g. "a lemon", "lemon zest of",
        "small lemon" for query "lemon") - residual near-duplicate
        canonical names from prep/size words folded into the parsed name
        (see recipe_scaling._ingredient_name_from_nlp_result) dominate
        the naive top-N with trivial self-matches otherwise, crowding out
        genuinely different but related ingredients."""
        ingredient = ingredient.strip().lower()
        ingredients, index, normalized = self._get_embedding_matrix()
        if ingredient not in index:
            return []
        target = normalized[index[ingredient]]
        sims = normalized @ target  # rows are pre-normalized, so this is cosine similarity directly

        scored = sorted(zip(sims.tolist(), ingredients), reverse=True)
        filtered = [
            (sim, other) for sim, other in scored
            if other != ingredient and ingredient not in other and other not in ingredient
        ]
        return [{'ingredient': other, 'similarity': round(sim, 3)} for sim, other in filtered[:limit]]

    def get_embedding_boost(self, seed_ingredients, candidate_ingredients, threshold: float = 0.6) -> float:
        """Sum of embedding similarities between seed and candidate
        ingredients above threshold - catches genuinely similar ingredients
        that never literally co-occurred in the corpus, which raw
        co-occurrence counting can't see at all."""
        total = 0.0
        for s in seed_ingredients:
            for c in candidate_ingredients:
                if s == c:
                    continue
                sim = self.get_ingredient_embedding_similarity(s, c)
                if sim >= threshold:
                    total += sim
        return total

    # ---- Companion recipe suggestions ----

    def suggest_companions(self, recipe: Recipe, recipe_db: RecipeDatabase, limit: int = 5) -> List[Dict]:
        """Suggest recipes that pair well with the given one, using shared
        ingredients weighted by how *distinctive* the pairing is (PMI-style:
        pair_count / how common the other ingredient is overall), plus
        cuisine match and a difficulty/time complement (if the seed recipe
        is long/hard, prefer quick/easy companions, and vice versa).

        Raw co-occurrence counts alone are dominated by ultra-common pantry
        staples (salt, pepper, butter) that pair with nearly everything,
        which made every seed recipe surface the same few generic dishes -
        normalizing by the other ingredient's overall frequency fixes that.

        Only considers recipes that actually have instructions (excludes the
        ingredients-only CC-BY-NC batch, since those can't be "cooked" as a
        companion dish).

        Ingredient sets (both the seed recipe's and every candidate's) come
        from recipe_ingredients.name, not raw recipes.ingredients text - as
        of 2026-07-12, ingredient_pairs/ingredient_totals/ingredient_embeddings
        are all keyed by that same parsed canonical name (see
        rebuild_ingredient_pairs()), so building seed/candidate sets from
        raw text ("4 blueberries" vs. the pairing table's "blueberries")
        silently matched nothing - this function was returning empty
        results for most recipes until caught by hand-testing after an
        embeddings rebuild that otherwise looked to have completed
        cleanly."""
        conn = sqlite3.connect(self.db_path)
        seed_ingredients = set(
            name.strip().lower() for (name,) in conn.execute(
                'SELECT name FROM recipe_ingredients WHERE recipe_id=? AND name IS NOT NULL', (recipe.id,)
            ) if name and name.strip()
        )
        conn.close()
        if not seed_ingredients:
            return []

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in seed_ingredients)
        cursor.execute(f'''
            SELECT p.ingredient_a, p.ingredient_b, p.pair_count, t.ingredient, t.total_count
            FROM ingredient_pairs p
            JOIN ingredient_totals t
              ON t.ingredient = CASE WHEN p.ingredient_a IN ({placeholders}) THEN p.ingredient_b ELSE p.ingredient_a END
            WHERE p.ingredient_a IN ({placeholders}) OR p.ingredient_b IN ({placeholders})
        ''', list(seed_ingredients) * 3)
        candidate_ingredients: Dict[str, float] = {}
        for a, b, pair_count, other, other_total in cursor.fetchall():
            if other in seed_ingredients or other_total <= 0:
                continue
            normalized = pair_count / other_total  # PMI-like: distinctive pairing, not just common ingredient
            candidate_ingredients[other] = max(candidate_ingredients.get(other, 0), normalized)
        conn.close()

        if not candidate_ingredients:
            return []

        seed_is_long = (recipe.prep_time + recipe.cook_time) > 45 or recipe.difficulty.lower() in ('hard', 'difficult')

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, title, description, ingredients, instructions, prep_time, cook_time,
                   total_time, servings, cuisine, difficulty, url, created_at, updated_at, license
            FROM recipes
            WHERE instructions IS NOT NULL AND instructions != '[]' AND id != ?
            LIMIT 5000
        ''', (recipe.id,))
        rows = cursor.fetchall()
        conn.close()

        # Same canonical-name fix as seed_ingredients above, batched in one
        # query keyed by recipe_id rather than N+1 queries per candidate -
        # up to 5000 candidates come out of the query above.
        candidate_names_by_id: Dict[int, set] = {}
        candidate_ids = [row[0] for row in rows]
        if candidate_ids:
            conn = sqlite3.connect(self.db_path)
            id_placeholders = ','.join('?' for _ in candidate_ids)
            for cid, name in conn.execute(
                f'SELECT recipe_id, name FROM recipe_ingredients WHERE recipe_id IN ({id_placeholders}) AND name IS NOT NULL',
                candidate_ids,
            ):
                if name and name.strip():
                    candidate_names_by_id.setdefault(cid, set()).add(name.strip().lower())
            conn.close()

        # Preload embeddings once (not per-candidate) to avoid opening a
        # SQLite connection per ingredient pair across potentially
        # thousands of candidates. Only fetches vectors for the seed
        # ingredients here; candidate-side vectors are fetched lazily below
        # and cached in the same dict, so each distinct ingredient is only
        # ever looked up once regardless of how many candidates share it.
        embedding_cache: Dict[str, Optional[list]] = {}

        def get_cached_vector(ingredient: str):
            if ingredient not in embedding_cache:
                conn2 = sqlite3.connect(self.db_path)
                cur2 = conn2.cursor()
                cur2.execute('SELECT vector FROM ingredient_embeddings WHERE ingredient=?', (ingredient,))
                row2 = cur2.fetchone()
                conn2.close()
                embedding_cache[ingredient] = json.loads(row2[0]) if row2 else None
            return embedding_cache[ingredient]

        EMBEDDING_SIMILARITY_THRESHOLD = 0.6

        def embedding_boost(cand_ingredients_set) -> float:
            """Same idea as get_embedding_boost, but using the local cache
            above instead of re-querying the DB per pair."""
            total = 0.0
            for s in seed_ingredients:
                s_vec = get_cached_vector(s)
                if s_vec is None:
                    continue
                for c in cand_ingredients_set:
                    if s == c:
                        continue
                    c_vec = get_cached_vector(c)
                    if c_vec is None:
                        continue
                    sim = cosine_similarity(s_vec, c_vec)
                    if sim >= EMBEDDING_SIMILARITY_THRESHOLD:
                        total += sim
            return total

        # Cheap first pass: co-occurrence overlap score only, for every
        # candidate that has any overlap at all. The embedding boost below
        # is expensive (a similarity check per seed x candidate ingredient
        # pair) - running it against every overlapping candidate made this
        # take 9-27 seconds against the real corpus. Instead, only run it
        # against the top EMBEDDING_BOOST_CANDIDATE_LIMIT candidates by
        # cheap co-occurrence score, bounding the expensive work regardless
        # of how many candidates pass the initial filter.
        EMBEDDING_BOOST_CANDIDATE_LIMIT = 50
        cheap_scored = []
        for row in rows:
            cand_ingredients = candidate_names_by_id.get(row[0])
            if not cand_ingredients:
                continue
            overlap = cand_ingredients & candidate_ingredients.keys()
            if not overlap:
                continue
            # Ingredient-pairing scores are normalized (roughly 0-1 per
            # ingredient), so these boosts are scaled to matter without
            # completely overriding the underlying pairing signal.
            co_occurrence_score = sum(candidate_ingredients[ing] for ing in overlap)
            cheap_scored.append((co_occurrence_score, cand_ingredients, row))

        cheap_scored.sort(key=lambda x: x[0], reverse=True)

        scored = []
        for co_occurrence_score, cand_ingredients, row in cheap_scored[:EMBEDDING_BOOST_CANDIDATE_LIMIT]:
            score = co_occurrence_score

            # Embedding boost: catches genuinely similar ingredients (e.g.
            # lime/lemon) that never literally co-occurred in the corpus,
            # which the co-occurrence score above can't see at all. Scoped
            # to candidates that already passed the co-occurrence overlap
            # check above, not a full independent embedding-only search -
            # a recipe with zero co-occurring ingredients still won't
            # surface here even if it's embedding-similar. That's a real
            # scope limit of this pass, not an oversight.
            score += embedding_boost(cand_ingredients)

            cand_cuisine = (row[9] or '').lower()
            if cand_cuisine and cand_cuisine == recipe.cuisine.lower():
                score += 0.5  # meaningful boost for matching cuisine

            cand_time = (row[5] or 0) + (row[6] or 0)
            cand_difficulty = (row[10] or '').lower()
            cand_is_quick_easy = cand_time <= 30 or cand_difficulty in ('easy', '')
            if seed_is_long and cand_is_quick_easy:
                score += 0.3  # complement a long/hard main with a quick/easy side

            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Flavor profile lookups are cheap (indexed point queries) but only
        # run against the final top `limit` results, not every scored
        # candidate - no need to pay the cost for recipes that won't be
        # shown. Top 3 flavors by tag count is a rough "what dominates this
        # dish" summary, not a full profile - matches the "surface flavor
        # data in suggestions" scope from SPEC.md; using flavor_pair_stats
        # to actively steer ranking is future recipe-creation-assistance
        # work, deliberately not done here.
        def top_flavors(recipe_id: int, n: int = 3) -> List[str]:
            profile = get_recipe_flavor_profile(recipe_id, db_path=self.db_path)
            if not profile or not profile['flavor_counts']:
                return []
            return [f for f, _ in sorted(profile['flavor_counts'].items(), key=lambda x: x[1], reverse=True)[:n]]

        seed_flavors = top_flavors(recipe.id) if recipe.id else []

        results = []
        for score, row in scored[:limit]:
            results.append({
                'id': row[0], 'title': row[1], 'cuisine': row[9],
                'difficulty': row[10], 'prep_time': row[5], 'cook_time': row[6],
                'score': score, 'flavor_profile': top_flavors(row[0]), 'seed_flavor_profile': seed_flavors,
            })
        return results
