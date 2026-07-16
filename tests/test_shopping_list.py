"""
Shopping list quantity-merging logic - the highest-risk area in this
feature, and the one that already produced one real bug this session
(merging into an existing NULL-quantity row raised a TypeError, see
test_merge_skips_existing_null_quantity_row below, which is a direct
regression test for that fix).
"""
import shopping_list
from conftest import make_recipe, set_ingredient_quantities


def test_add_recipe_creates_one_line_per_ingredient(db_path, recipe_db):
    recipe_id = make_recipe(recipe_db, "Pancakes", ["2 cups flour", "1 egg"])
    set_ingredient_quantities(db_path, recipe_id, [(2.0, "cup", "flour"), (1.0, None, "egg")])

    list_id = shopping_list.create_list("Test", db_path=db_path)
    count = shopping_list.add_recipe_to_list(list_id, recipe_id, recipe_db, db_path=db_path)

    items = shopping_list.get_items(list_id, db_path=db_path)
    assert count == 2
    assert len(items) == 2
    names = {i["name"] for i in items}
    assert names == {"flour", "egg"}


def test_matching_name_and_unit_merge_into_one_summed_line(db_path, recipe_db):
    r1 = make_recipe(recipe_db, "Recipe A", ["2 cups flour"])
    set_ingredient_quantities(db_path, r1, [(2.0, "cup", "flour")])
    r2 = make_recipe(recipe_db, "Recipe B", ["3 cups flour"])
    set_ingredient_quantities(db_path, r2, [(3.0, "cup", "flour")])

    list_id = shopping_list.create_list("Test", db_path=db_path)
    shopping_list.add_recipe_to_list(list_id, r1, recipe_db, db_path=db_path)
    shopping_list.add_recipe_to_list(list_id, r2, recipe_db, db_path=db_path)

    items = shopping_list.get_items(list_id, db_path=db_path)
    assert len(items) == 1
    assert items[0]["name"] == "flour"
    assert items[0]["unit"] == "cup"
    assert items[0]["quantity"] == 5.0


def test_same_ingredient_different_units_stay_as_separate_lines(db_path, recipe_db):
    r1 = make_recipe(recipe_db, "Recipe A", ["2 cups flour"])
    set_ingredient_quantities(db_path, r1, [(2.0, "cup", "flour")])
    r2 = make_recipe(recipe_db, "Recipe B", ["3 tbsp flour"])
    set_ingredient_quantities(db_path, r2, [(3.0, "tbsp", "flour")])

    list_id = shopping_list.create_list("Test", db_path=db_path)
    shopping_list.add_recipe_to_list(list_id, r1, recipe_db, db_path=db_path)
    shopping_list.add_recipe_to_list(list_id, r2, recipe_db, db_path=db_path)

    items = shopping_list.get_items(list_id, db_path=db_path)
    assert len(items) == 2
    units = {i["unit"] for i in items}
    assert units == {"cup", "tbsp"}


def test_merge_skips_existing_null_quantity_row(db_path, recipe_db):
    """Regression test: an ingredient whose parse failed (quantity=None)
    is added as its own line. Adding a *second* occurrence of the same
    (name, unit) that DOES have a parseable quantity must not try to sum
    into that NULL row - it previously raised
    `TypeError: unsupported operand type(s) for +: 'NoneType' and 'float'`
    before the merge query was fixed to require the existing row's
    quantity to be non-null."""
    r1 = make_recipe(recipe_db, "Recipe A", ["a pinch of salt"])
    set_ingredient_quantities(db_path, r1, [(None, None, "salt")])
    r2 = make_recipe(recipe_db, "Recipe B", ["2 tsp salt"])
    set_ingredient_quantities(db_path, r2, [(2.0, "tsp", "salt")])

    list_id = shopping_list.create_list("Test", db_path=db_path)
    shopping_list.add_recipe_to_list(list_id, r1, recipe_db, db_path=db_path)
    # This call must not raise.
    shopping_list.add_recipe_to_list(list_id, r2, recipe_db, db_path=db_path)

    items = shopping_list.get_items(list_id, db_path=db_path)
    assert len(items) == 2
    quantities = [i["quantity"] for i in items]
    assert None in quantities
    assert 2.0 in quantities


def test_checked_items_are_not_merge_targets(db_path, recipe_db):
    """A checked-off line represents "already bought" - a later recipe
    calling for the same ingredient should start a fresh unchecked line,
    not silently re-add quantity to something already checked off."""
    r1 = make_recipe(recipe_db, "Recipe A", ["2 cups flour"])
    set_ingredient_quantities(db_path, r1, [(2.0, "cup", "flour")])
    r2 = make_recipe(recipe_db, "Recipe B", ["3 cups flour"])
    set_ingredient_quantities(db_path, r2, [(3.0, "cup", "flour")])

    list_id = shopping_list.create_list("Test", db_path=db_path)
    shopping_list.add_recipe_to_list(list_id, r1, recipe_db, db_path=db_path)
    first_item = shopping_list.get_items(list_id, db_path=db_path)[0]
    shopping_list.set_item_checked(first_item["id"], True, db_path=db_path)

    shopping_list.add_recipe_to_list(list_id, r2, recipe_db, db_path=db_path)

    items = shopping_list.get_items(list_id, db_path=db_path)
    assert len(items) == 2
    checked = [i for i in items if i["checked"]]
    unchecked = [i for i in items if not i["checked"]]
    assert len(checked) == 1 and checked[0]["quantity"] == 2.0
    assert len(unchecked) == 1 and unchecked[0]["quantity"] == 3.0


def test_manual_item_add_and_remove(db_path):
    list_id = shopping_list.create_list("Test", db_path=db_path)
    item_id = shopping_list.add_manual_item(list_id, "paper towels", db_path=db_path)

    items = shopping_list.get_items(list_id, db_path=db_path)
    assert len(items) == 1
    assert items[0]["name"] == "paper towels"
    assert items[0]["quantity"] is None

    assert shopping_list.remove_item(item_id, db_path=db_path) is True
    assert shopping_list.get_items(list_id, db_path=db_path) == []


def test_add_plan_to_list_merges_across_recipes_in_the_plan(db_path, recipe_db, meal_db):
    r1 = make_recipe(recipe_db, "Recipe A", ["2 cups flour"])
    set_ingredient_quantities(db_path, r1, [(2.0, "cup", "flour")])
    r2 = make_recipe(recipe_db, "Recipe B", ["1 cup flour"])
    set_ingredient_quantities(db_path, r2, [(1.0, "cup", "flour")])

    plan_id = meal_db.create_plan("Test plan")
    meal_db.add_recipe_to_plan(plan_id, r1)
    meal_db.add_recipe_to_plan(plan_id, r2)

    list_id = shopping_list.create_list("Test", db_path=db_path)
    total = shopping_list.add_plan_to_list(list_id, plan_id, meal_db, recipe_db, db_path=db_path)

    items = shopping_list.get_items(list_id, db_path=db_path)
    assert total == 2  # two ingredient lines processed
    assert len(items) == 1  # merged into one
    assert items[0]["quantity"] == 3.0


def test_delete_list_removes_its_items(db_path):
    list_id = shopping_list.create_list("Test", db_path=db_path)
    shopping_list.add_manual_item(list_id, "napkins", db_path=db_path)

    assert shopping_list.delete_list(list_id, db_path=db_path) is True
    assert shopping_list.get_list(list_id, db_path=db_path) is None
