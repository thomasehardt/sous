"""recipe_flavor_index.py precomputes per-recipe flavor weights into a
recipe_flavors table so intent search (query_planner.py) can filter/rank
by flavor with an indexed SQL query instead of re-deriving each recipe's
profile live. Covers both halves: the build step (build_recipe_flavor_index)
and the read step (find_recipes_by_flavors), including its cuisine/
max_total_time narrowing and its requirement that a recipe actually have
instructions to be returned (matches this project's "don't suggest a
recipe with no method" convention elsewhere in discovery).
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from conftest import make_recipe, set_ingredient_quantities
from flavor_tagging import init_ingredient_flavors_table
from recipe_flavor_index import build_recipe_flavor_index, find_recipes_by_flavors


def tag_ingredient(db_path, ingredient, flavors):
    conn = sqlite3.connect(db_path)
    for flavor in flavors:
        conn.execute(
            "INSERT OR IGNORE INTO ingredient_flavors (ingredient, flavor) VALUES (?, ?)",
            (ingredient, flavor),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def flavor_tables(db_path):
    init_ingredient_flavors_table(db_path)


def make_indexable_recipe(recipe_db, db_path, title, ingredient_name, unit="cup",
                           cuisine=None, total_time=None):
    recipe_id = make_recipe(
        recipe_db, title, [f"1 {unit} {ingredient_name}"],
        instructions=["Combine everything.", "Serve."],
    )
    set_ingredient_quantities(db_path, recipe_id, [(1.0, unit, ingredient_name)])
    if cuisine is not None or total_time is not None:
        conn = sqlite3.connect(db_path)
        if cuisine is not None:
            conn.execute("UPDATE recipes SET cuisine = ? WHERE id = ?", (cuisine, recipe_id))
        if total_time is not None:
            conn.execute("UPDATE recipes SET total_time = ? WHERE id = ?", (total_time, recipe_id))
        conn.commit()
        conn.close()
    return recipe_id


class TestBuildRecipeFlavorIndex:
    def test_indexes_recipe_ingredient_flavor_rows(self, db_path, recipe_db):
        recipe_id = make_indexable_recipe(recipe_db, db_path, "Sweet Toast", "honey")
        tag_ingredient(db_path, "honey", ["sweet"])

        count = build_recipe_flavor_index(db_path)
        assert count == 1

        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT recipe_id, flavor, weight FROM recipe_flavors").fetchall()
        conn.close()
        assert rows == [(recipe_id, "sweet", 1)]

    def test_is_safe_to_rerun_and_fully_replaces_prior_contents(self, db_path, recipe_db):
        recipe_id = make_indexable_recipe(recipe_db, db_path, "Sweet Toast", "honey")
        tag_ingredient(db_path, "honey", ["sweet"])
        build_recipe_flavor_index(db_path)

        # delete the recipe's ingredient row entirely, then rebuild - the
        # stale (recipe_id, 'sweet') row must not survive a rebuild.
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
        conn.commit()
        conn.close()

        count = build_recipe_flavor_index(db_path)
        assert count == 0

    def test_weight_reflects_ingredient_count_contributing_a_flavor(self, db_path, recipe_db):
        recipe_id = make_recipe(
            recipe_db, "Double Sweet", ["1 cup honey", "1 cup sugar"],
            instructions=["Mix.", "Serve."],
        )
        set_ingredient_quantities(db_path, recipe_id, [(1.0, "cup", "honey"), (1.0, "cup", "sugar")])
        tag_ingredient(db_path, "honey", ["sweet"])
        tag_ingredient(db_path, "sugar", ["sweet"])

        build_recipe_flavor_index(db_path)
        conn = sqlite3.connect(db_path)
        weight = conn.execute(
            "SELECT weight FROM recipe_flavors WHERE recipe_id = ? AND flavor = 'sweet'", (recipe_id,)
        ).fetchone()[0]
        conn.close()
        assert weight == 2


class TestFindRecipesByFlavors:
    def test_ranks_by_flavor_match_count(self, db_path, recipe_db):
        both = make_recipe(
            recipe_db, "Sweet and Sour Ribs", ["1 cup honey", "1 cup vinegar"],
            instructions=["Combine everything.", "Serve."],
        )
        set_ingredient_quantities(db_path, both, [(1.0, "cup", "honey"), (1.0, "cup", "vinegar")])
        one = make_indexable_recipe(recipe_db, db_path, "Just Sweet", "sugar")

        tag_ingredient(db_path, "honey", ["sweet"])
        tag_ingredient(db_path, "vinegar", ["sour"])
        tag_ingredient(db_path, "sugar", ["sweet"])
        build_recipe_flavor_index(db_path)

        results = find_recipes_by_flavors(["sweet", "sour"], db_path=db_path)
        ids = [r["recipe_id"] for r in results]
        assert ids[0] == both
        assert one in ids

    def test_excludes_recipes_with_no_instructions(self, db_path, recipe_db):
        recipe_id = make_recipe(recipe_db, "No Method Yet", ["1 cup honey"], instructions=[])
        set_ingredient_quantities(db_path, recipe_id, [(1.0, "cup", "honey")])
        tag_ingredient(db_path, "honey", ["sweet"])
        build_recipe_flavor_index(db_path)

        results = find_recipes_by_flavors(["sweet"], db_path=db_path)
        assert results == []

    def test_cuisine_filter(self, db_path, recipe_db):
        thai = make_indexable_recipe(recipe_db, db_path, "Thai Curry", "coconut milk", cuisine="Thai")
        indian = make_indexable_recipe(recipe_db, db_path, "Indian Curry", "coconut milk", cuisine="Indian")
        tag_ingredient(db_path, "coconut milk", ["sweet"])
        build_recipe_flavor_index(db_path)

        results = find_recipes_by_flavors(["sweet"], db_path=db_path, cuisine="thai")
        ids = [r["recipe_id"] for r in results]
        assert ids == [thai]
        assert indian not in ids

    def test_max_total_time_filter(self, db_path, recipe_db):
        quick = make_indexable_recipe(recipe_db, db_path, "Quick Snack", "honey", total_time=10)
        slow = make_indexable_recipe(recipe_db, db_path, "Slow Roast", "honey", total_time=240)
        tag_ingredient(db_path, "honey", ["sweet"])
        build_recipe_flavor_index(db_path)

        results = find_recipes_by_flavors(["sweet"], db_path=db_path, max_total_time=30)
        ids = [r["recipe_id"] for r in results]
        assert ids == [quick]
        assert slow not in ids

    def test_empty_flavor_list_returns_empty(self, db_path, recipe_db):
        assert find_recipes_by_flavors([], db_path=db_path) == []

    def test_limit_caps_results(self, db_path, recipe_db):
        for i in range(3):
            make_indexable_recipe(recipe_db, db_path, f"Sweet Thing {i}", "honey")
        tag_ingredient(db_path, "honey", ["sweet"])
        build_recipe_flavor_index(db_path)

        results = find_recipes_by_flavors(["sweet"], db_path=db_path, limit=2)
        assert len(results) == 2
