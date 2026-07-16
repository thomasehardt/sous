"""flavor_queries.py aggregates flavor tags across a recipe/cuisine/meal
plan's ingredients. Regression coverage for the bug fixed this session
(70bbde8): profiles must be keyed off recipe_ingredients.name (the
canonical parsed name) rather than raw recipes.ingredients text, since
ingredient_flavors rows are keyed on the bare parsed name too - "4 fresh
blueberries" never matches an ingredient_flavors row for "blueberries".

Tables are built the same way the app builds them (RecipeDatabase +
flavor_tagging.init_ingredient_flavors_table + MealPlanDatabase), and
ingredient_flavors rows are seeded directly with known tags rather than
going through the real LLM tagger, decoupling "does the aggregation
logic work" from "is the LLM tagging accurate" (already validated
elsewhere - see docs/papers).
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from conftest import make_recipe, set_ingredient_quantities
from flavor_tagging import init_ingredient_flavors_table
from meal_planner import MealPlanDatabase
from flavor_queries import (
    get_ingredient_flavor_profile,
    get_recipe_flavor_profile,
    get_cuisine_flavor_profile,
    get_meal_plan_flavor_profile,
)


def tag_ingredient(db_path, ingredient, flavors, tagged=True):
    conn = sqlite3.connect(db_path)
    for flavor in flavors:
        conn.execute(
            "INSERT OR IGNORE INTO ingredient_flavors (ingredient, flavor) VALUES (?, ?)",
            (ingredient, flavor),
        )
    if tagged:
        conn.execute(
            "INSERT OR IGNORE INTO ingredient_flavor_tagged (ingredient) VALUES (?)", (ingredient,)
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def flavor_tables(db_path):
    init_ingredient_flavors_table(db_path)


class TestIngredientFlavorProfile:
    def test_known_ingredient_returns_its_flavors(self, db_path):
        tag_ingredient(db_path, "blueberries", ["sweet", "fruity"])
        profile = get_ingredient_flavor_profile("blueberries", db_path=db_path)
        assert profile["flavors"] == ["fruity", "sweet"]  # ORDER BY flavor
        assert profile["tagged"] is True

    def test_normalizes_case_and_whitespace(self, db_path):
        tag_ingredient(db_path, "garlic", ["pungent"])
        profile = get_ingredient_flavor_profile("  Garlic  ", db_path=db_path)
        assert profile["ingredient"] == "garlic"
        assert profile["flavors"] == ["pungent"]

    def test_untagged_ingredient(self, db_path):
        profile = get_ingredient_flavor_profile("mystery root", db_path=db_path)
        assert profile["flavors"] == []
        assert profile["tagged"] is False


class TestRecipeFlavorProfile:
    def test_uses_canonical_parsed_names_not_raw_ingredient_text(self, db_path, recipe_db):
        # This is the exact bug fixed in 70bbde8: raw ingredient text
        # ("4 fresh blueberries") carries a quantity/descriptor prefix
        # that would never match an ingredient_flavors row keyed on the
        # bare canonical name ("blueberries").
        recipe_id = make_recipe(recipe_db, "Blueberry Muffins", ["4 fresh blueberries"])
        set_ingredient_quantities(db_path, recipe_id, [(4.0, None, "blueberries")])
        tag_ingredient(db_path, "blueberries", ["sweet", "fruity"])

        profile = get_recipe_flavor_profile(recipe_id, db_path=db_path)
        assert profile["flavor_counts"] == {"sweet": 1, "fruity": 1}
        assert profile["untagged_ingredients"] == []

    def test_untagged_ingredient_listed_separately(self, db_path, recipe_db):
        recipe_id = make_recipe(recipe_db, "Mystery Stew", ["1 cup mystery root"])
        set_ingredient_quantities(db_path, recipe_id, [(1.0, "cup", "mystery root")])

        profile = get_recipe_flavor_profile(recipe_id, db_path=db_path)
        assert profile["flavor_counts"] == {}
        assert profile["untagged_ingredients"] == ["mystery root"]

    def test_nonexistent_recipe_returns_none(self, db_path, recipe_db):
        assert get_recipe_flavor_profile(99999, db_path=db_path) is None

    def test_duplicate_ingredient_names_counted_once(self, db_path, recipe_db):
        # _canonical_ingredients_for_recipe dedupes via SELECT DISTINCT -
        # two ingredient lines both parsing to "salt" should only
        # contribute one count to flavor_counts, not two.
        recipe_id = make_recipe(recipe_db, "Salty Thing", ["1 tsp salt", "1 pinch salt"])
        set_ingredient_quantities(db_path, recipe_id, [(1.0, "tsp", "salt"), (1.0, "pinch", "salt")])
        tag_ingredient(db_path, "salt", ["salty"])

        profile = get_recipe_flavor_profile(recipe_id, db_path=db_path)
        assert profile["ingredient_count"] == 1
        assert profile["flavor_counts"] == {"salty": 1}


class TestCuisineFlavorProfile:
    def test_matches_one_component_of_comma_separated_field(self, db_path, recipe_db):
        # cuisine is a comma-separated multi-value field - "italian" must
        # match a recipe tagged "Italian, Mediterranean" but not a recipe
        # merely containing "italian" as a substring of something else
        # (e.g. "Italian-American" should not match a bare "italian" query
        # unless it's an exact comma-split component).
        r1 = make_recipe(recipe_db, "Pasta", ["1 cup flour"])
        set_ingredient_quantities(db_path, r1, [(1.0, "cup", "flour")])
        r2 = make_recipe(recipe_db, "Fusion Dish", ["1 cup rice"])
        set_ingredient_quantities(db_path, r2, [(1.0, "cup", "rice")])

        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE recipes SET cuisine = ? WHERE id = ?", ("Italian, Mediterranean", r1))
        conn.execute("UPDATE recipes SET cuisine = ? WHERE id = ?", ("Italian-American", r2))
        conn.commit()
        conn.close()

        tag_ingredient(db_path, "flour", ["starchy"])
        tag_ingredient(db_path, "rice", ["starchy"])

        profile = get_cuisine_flavor_profile("italian", db_path=db_path)
        assert profile["recipe_count"] == 1
        assert profile["flavor_counts"] == {"starchy": 1}

    def test_limit_caps_recipe_count(self, db_path, recipe_db):
        rids = []
        for i in range(3):
            rid = make_recipe(recipe_db, f"Dish {i}", ["1 cup flour"])
            set_ingredient_quantities(db_path, rid, [(1.0, "cup", "flour")])
            rids.append(rid)
        conn = sqlite3.connect(db_path)
        for rid in rids:
            conn.execute("UPDATE recipes SET cuisine = ? WHERE id = ?", ("mexican", rid))
        conn.commit()
        conn.close()
        tag_ingredient(db_path, "flour", ["starchy"])

        profile = get_cuisine_flavor_profile("mexican", db_path=db_path, limit=2)
        assert profile["recipe_count"] == 2

    def test_no_matches_returns_zero_count(self, db_path, recipe_db):
        profile = get_cuisine_flavor_profile("klingon", db_path=db_path)
        assert profile["recipe_count"] == 0
        assert profile["flavor_counts"] == {}


class TestMealPlanFlavorProfile:
    def test_aggregates_across_plan_recipes(self, db_path, recipe_db, meal_db):
        r1 = make_recipe(recipe_db, "Toast", ["1 slice bread"])
        set_ingredient_quantities(db_path, r1, [(1.0, "slice", "bread")])
        r2 = make_recipe(recipe_db, "Jam", ["1 tbsp jam"])
        set_ingredient_quantities(db_path, r2, [(1.0, "tbsp", "jam")])
        tag_ingredient(db_path, "bread", ["savory"])
        tag_ingredient(db_path, "jam", ["sweet"])

        plan_id = meal_db.create_plan("Breakfast")
        meal_db.add_recipe_to_plan(plan_id, r1)
        meal_db.add_recipe_to_plan(plan_id, r2)

        profile = get_meal_plan_flavor_profile(plan_id, db_path=db_path)
        assert profile["flavor_counts"] == {"savory": 1, "sweet": 1}
        assert set(profile["per_recipe"].keys()) == {r1, r2}

    def test_nonexistent_plan_returns_none(self, db_path, recipe_db, meal_db):
        assert get_meal_plan_flavor_profile(99999, db_path=db_path) is None

    def test_empty_plan_returns_empty_counts(self, db_path, recipe_db, meal_db):
        plan_id = meal_db.create_plan("Empty Plan")
        profile = get_meal_plan_flavor_profile(plan_id, db_path=db_path)
        assert profile["recipe_ids"] == []
        assert profile["flavor_counts"] == {}
