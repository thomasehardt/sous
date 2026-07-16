"""
Backward-scheduling and cross-recipe conflict detection - the third
highest-risk area. Step durations/types are extracted from real free
text via the actual regex/keyword heuristics (not stubbed), since that
extraction logic is itself part of what's being tested; only the
resulting *schedule and conflict* computation is what these tests focus
their assertions on.
"""
from datetime import datetime, timedelta

from meal_planner import extract_step_duration, classify_step_type
from conftest import make_recipe


# ---- extract_step_duration ----

def test_extract_step_duration_minutes():
    assert extract_step_duration("Stir for 10 minutes.") == 10


def test_extract_step_duration_hours_converted_to_minutes():
    assert extract_step_duration("Marinate for 2 hours.") == 120


def test_extract_step_duration_overnight_is_eight_hours():
    assert extract_step_duration("Let sit overnight in the fridge.") == 480


def test_extract_step_duration_no_mention_uses_default():
    assert extract_step_duration("Season with salt and pepper.") == 5


def test_extract_step_duration_range_uses_first_number():
    assert extract_step_duration("Bake for 25 to 30 minutes.") == 25


# ---- classify_step_type ----

def test_classify_step_type_bake_is_passive():
    assert classify_step_type("Bake for 20 minutes.") == "passive"


def test_classify_step_type_marinate_is_passive():
    assert classify_step_type("Marinate the chicken for 2 hours.") == "passive"


def test_classify_step_type_no_passive_keyword_defaults_active():
    assert classify_step_type("Whisk the eggs vigorously.") == "active"


# ---- backward_schedule_recipe ----

def test_backward_schedule_recipe_starts_before_eat_time_by_total_duration(db_path, recipe_db, meal_db):
    recipe_id = make_recipe(
        recipe_db, "Two Step Dish",
        ["1 egg"],
        instructions=["Whisk for 10 minutes.", "Bake for 20 minutes."],
    )
    recipe = recipe_db.get_recipe(recipe_id)
    eat_time = datetime(2026, 1, 1, 18, 0)

    schedule = meal_db.backward_schedule_recipe(recipe, eat_time)

    assert len(schedule) == 2
    assert schedule[0]["start_time"] == eat_time - timedelta(minutes=30)
    assert schedule[0]["end_time"] == eat_time - timedelta(minutes=20)
    assert schedule[0]["step_type"] == "active"
    assert schedule[1]["start_time"] == eat_time - timedelta(minutes=20)
    assert schedule[1]["end_time"] == eat_time
    assert schedule[1]["step_type"] == "passive"


def test_backward_schedule_recipe_with_no_instructions_is_empty(db_path, recipe_db, meal_db):
    recipe_id = make_recipe(recipe_db, "No Instructions", ["1 egg"], instructions=[])
    recipe = recipe_db.get_recipe(recipe_id)

    assert meal_db.backward_schedule_recipe(recipe, datetime(2026, 1, 1, 18, 0)) == []


# ---- backward_schedule_plan: conflict detection ----

def test_overlapping_active_steps_across_recipes_are_flagged(db_path, recipe_db, meal_db):
    # Both recipes have one 15-minute active step ending exactly at
    # eat_time - their active windows fully overlap.
    r1 = make_recipe(recipe_db, "Stir Fry", ["oil"], instructions=["Stir constantly for 15 minutes."])
    r2 = make_recipe(recipe_db, "Whisked Sauce", ["cream"], instructions=["Whisk vigorously for 15 minutes."])

    plan_id = meal_db.create_plan("Test plan")
    meal_db.add_recipe_to_plan(plan_id, r1)
    meal_db.add_recipe_to_plan(plan_id, r2)

    result = meal_db.backward_schedule_plan(plan_id, recipe_db, datetime(2026, 1, 1, 18, 0))

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert "Stir Fry" in conflict["a"] or "Stir Fry" in conflict["b"]
    assert "Whisked Sauce" in conflict["a"] or "Whisked Sauce" in conflict["b"]


def test_overlapping_passive_steps_are_not_flagged(db_path, recipe_db, meal_db):
    # Two ovens/fridges can run at once - only *active* (hands-on) steps
    # can conflict, by design.
    r1 = make_recipe(recipe_db, "Baked Dish", ["flour"], instructions=["Bake for 15 minutes."])
    r2 = make_recipe(recipe_db, "Chilled Dish", ["cream"], instructions=["Chill in the fridge for 15 minutes."])

    plan_id = meal_db.create_plan("Test plan")
    meal_db.add_recipe_to_plan(plan_id, r1)
    meal_db.add_recipe_to_plan(plan_id, r2)

    result = meal_db.backward_schedule_plan(plan_id, recipe_db, datetime(2026, 1, 1, 18, 0))

    assert result["conflicts"] == []


def test_non_overlapping_active_steps_are_not_flagged(db_path, recipe_db, meal_db):
    # Every recipe's *last* step always ends exactly at eat_time (that's
    # how backward scheduling works), so to get a genuinely non-
    # overlapping pair, r2's active step must NOT be its last step - it
    # needs a passive step after it to push it earlier in absolute time.
    # r1: single active 5-min step -> [eat-5, eat].
    # r2: active 5-min step, then a passive 15-min bake -> the active
    #     step lands at [eat-20, eat-15], well clear of r1's window.
    r1 = make_recipe(recipe_db, "Quick Chop", ["onion"], instructions=["Chop onions for 5 minutes."])
    r2 = make_recipe(
        recipe_db, "Prep Then Bake", ["garlic"],
        instructions=["Season the garlic.", "Bake for 15 minutes."],
    )

    plan_id = meal_db.create_plan("Test plan")
    meal_db.add_recipe_to_plan(plan_id, r1)
    meal_db.add_recipe_to_plan(plan_id, r2)

    result = meal_db.backward_schedule_plan(plan_id, recipe_db, datetime(2026, 1, 1, 18, 0))

    season_steps = [s for s in result["timeline"] if "Season" in s["text"]]
    chop_steps = [s for s in result["timeline"] if "Chop onions" in s["text"]]
    assert len(season_steps) == 1 and len(chop_steps) == 1
    assert season_steps[0]["step_type"] == "active"
    assert season_steps[0]["end_time"] <= chop_steps[0]["start_time"]
    assert result["conflicts"] == []


def test_same_recipe_added_twice_never_conflicts_with_itself(db_path, recipe_db, meal_db):
    """A doubled recipe (added to the plan twice, e.g. for a bigger batch)
    schedules two independent copies of the same active step at the same
    time - without the explicit same-recipe_id skip in the conflict
    detector, this would falsely flag "the recipe conflicts with itself.\""""
    r1 = make_recipe(recipe_db, "Soup", ["broth"], instructions=["Stir for 15 minutes."])

    plan_id = meal_db.create_plan("Double batch")
    meal_db.add_recipe_to_plan(plan_id, r1)
    meal_db.add_recipe_to_plan(plan_id, r1)

    result = meal_db.backward_schedule_plan(plan_id, recipe_db, datetime(2026, 1, 1, 18, 0))

    assert len(result["timeline"]) == 2
    assert result["conflicts"] == []


def test_recipe_with_no_instructions_is_skipped_not_erroring(db_path, recipe_db, meal_db):
    r1 = make_recipe(recipe_db, "Has Steps", ["egg"], instructions=["Whisk for 5 minutes."])
    r2 = make_recipe(recipe_db, "No Steps", ["egg"], instructions=[])

    plan_id = meal_db.create_plan("Test plan")
    meal_db.add_recipe_to_plan(plan_id, r1)
    meal_db.add_recipe_to_plan(plan_id, r2)

    result = meal_db.backward_schedule_plan(plan_id, recipe_db, datetime(2026, 1, 1, 18, 0))

    assert "No Steps" in result["skipped_no_instructions"]
    assert len(result["timeline"]) == 1
