"""
Shared fixtures. Every test gets its own fresh SQLite file under pytest's
tmp_path - never the real recipes.db. Schema is created the same way the
app itself creates it (RecipeDatabase.__init__ / MealPlanDatabase.__init__
/ each feature module's own init_*_table()), not hand-rolled CREATE TABLE
statements in the test suite, so a real schema change is caught by tests
breaking rather than tests and app silently drifting apart.

Where a test needs a recipe with *specific* structured ingredient
quantities/units (not whatever the real NLP parser happens to produce for
some free-text string), it goes through save_recipe() for everything else
(id assignment, FTS sync, JSON encoding) and then directly overwrites the
recipe_ingredients rows - this decouples "does the merge/scheduling logic
work" from "is the ingredient parser accurate," which is already exercised
elsewhere.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from recipe_model import Recipe, RecipeDatabase
from meal_planner import MealPlanDatabase


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test_recipes.db")


@pytest.fixture
def recipe_db(db_path) -> RecipeDatabase:
    return RecipeDatabase(db_path)


@pytest.fixture
def meal_db(db_path, recipe_db) -> MealPlanDatabase:
    return MealPlanDatabase(db_path)


def make_recipe(recipe_db: RecipeDatabase, title: str, ingredients: list, instructions: list = None,
                 servings: int = 1, prep_time: int = 0, cook_time: int = 0) -> int:
    """Creates and saves a recipe through the real save path (so FTS/
    structured-ingredient sync all run for real), returns its id."""
    recipe = Recipe(
        title=title,
        ingredients=ingredients,
        instructions=instructions or [],
        servings=servings,
        prep_time=prep_time,
        cook_time=cook_time,
    )
    return recipe_db.save_recipe(recipe)


def set_ingredient_quantities(db_path: str, recipe_id: int, overrides: list):
    """Directly overwrites recipe_ingredients rows for a recipe with known
    (quantity, unit, name) values, in position order - bypasses the NLP
    parser's actual guess so merge/aggregation tests are deterministic and
    independent of parser accuracy."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        'SELECT id FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position', (recipe_id,)
    ).fetchall()
    assert len(rows) == len(overrides), (
        f"recipe {recipe_id} has {len(rows)} parsed ingredient rows, "
        f"but {len(overrides)} overrides were given - check the ingredient list matches"
    )
    for (row_id,), (quantity, unit, name) in zip(rows, overrides):
        conn.execute(
            'UPDATE recipe_ingredients SET quantity = ?, unit = ?, name = ? WHERE id = ?',
            (quantity, unit, name, row_id),
        )
    conn.commit()
    conn.close()
