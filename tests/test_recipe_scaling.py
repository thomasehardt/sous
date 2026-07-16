"""recipe_scaling.py is almost entirely pure functions (no DB) - the raw-text
quantity parser/formatter/scaler, and the structured-ingredient equivalents
built on top of it. Exercises the documented edge cases directly (unicode
fractions, mixed numbers, decimals, ranges, unit-less lines) rather than
just the happy path, since this module explicitly documents itself as a
heuristic with known blind spots.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from recipe_scaling import (
    parse_quantity,
    format_quantity,
    scale_ingredient,
    scale_ingredients,
    scale_recipe_to_servings,
    format_structured_quantity,
    scale_structured_ingredient,
    scale_structured_ingredients,
    scale_recipe_to_servings_structured,
    is_ingredient_section_header,
    _parse_ingredient_heuristic,
    _pluralize_unit,
)


class TestParseQuantity:
    def test_plain_integer(self):
        assert parse_quantity("2 cups flour") == (2.0, "cups flour")

    def test_decimal(self):
        assert parse_quantity("1.5 cups sugar") == (1.5, "cups sugar")

    def test_simple_fraction(self):
        assert parse_quantity("1/2 cup butter") == (0.5, "cup butter")

    def test_mixed_number(self):
        assert parse_quantity("1 1/2 cups milk") == (1.5, "cups milk")

    def test_unicode_fraction_alone(self):
        assert parse_quantity("½ cup oil") == (0.5, "cup oil")

    def test_whole_plus_unicode_fraction(self):
        value, remainder = parse_quantity("2½ cups rice")
        assert value == 2.5
        assert remainder == "cups rice"

    def test_range_uses_leading_number(self):
        # "3-4 apples" - only the leading quantity is used, matching the
        # module's documented scope for compound/range amounts.
        value, remainder = parse_quantity("3-4 apples")
        assert value == 3.0
        assert remainder == "apples"

    def test_no_quantity_returns_none_and_original_text(self):
        assert parse_quantity("Salt to taste") == (None, "Salt to taste")

    def test_empty_string(self):
        assert parse_quantity("") == (None, "")


class TestFormatQuantity:
    def test_whole_number(self):
        assert format_quantity(2.0) == "2"

    def test_common_fraction(self):
        assert format_quantity(0.5) == "1/2"

    def test_whole_plus_common_fraction(self):
        assert format_quantity(1.5) == "1 1/2"

    def test_uncommon_decimal_falls_back_to_decimal_text(self):
        # 0.37 doesn't match any of the _COMMON_FRACTIONS within tolerance
        assert format_quantity(0.37) == "0.37"

    def test_near_whole_rounds_to_whole(self):
        # within the 0.01 tolerance band
        assert format_quantity(2.999) == "3"


class TestScaleIngredient:
    def test_doubles_a_simple_quantity(self):
        assert scale_ingredient("2 cups flour", 2.0) == "4 cups flour"

    def test_halves_a_fraction(self):
        assert scale_ingredient("1 cup sugar", 0.5) == "1/2 cup sugar"

    def test_no_quantity_passed_through_unscaled(self):
        assert scale_ingredient("Salt to taste", 3.0) == "Salt to taste"

    def test_scale_ingredients_list(self):
        result = scale_ingredients(["2 eggs", "1 cup milk"], 2.0)
        assert result == ["4 eggs", "2 cup milk"]


class TestScaleRecipeToServings:
    def test_scales_by_ratio(self):
        result = scale_recipe_to_servings(["2 cups flour"], current_servings=4, target_servings=8)
        assert result == ["4 cups flour"]

    def test_zero_current_servings_treated_as_one(self):
        # current_servings <= 0 is guarded to avoid a ZeroDivisionError
        result = scale_recipe_to_servings(["2 cups flour"], current_servings=0, target_servings=2)
        assert result == ["4 cups flour"]


class TestStructuredIngredientHeuristic:
    def test_quantity_unit_name_all_parsed(self):
        parsed = _parse_ingredient_heuristic("2 cups all-purpose flour")
        assert parsed["quantity"] == 2.0
        assert parsed["unit"] == "cup"
        assert parsed["name"] == "all-purpose flour"

    def test_unit_alias_maps_to_canonical(self):
        parsed = _parse_ingredient_heuristic("3 tbsps olive oil")
        assert parsed["unit"] == "tbsp"
        assert parsed["name"] == "olive oil"

    def test_no_recognizable_unit_falls_back_to_full_remainder_as_name(self):
        parsed = _parse_ingredient_heuristic("Salt to taste")
        assert parsed["quantity"] is None
        assert parsed["unit"] is None
        assert parsed["name"] == "Salt to taste"

    def test_never_raises_and_never_drops_text(self):
        # raw_text is always preserved verbatim regardless of what could be parsed
        parsed = _parse_ingredient_heuristic("")
        assert parsed["raw_text"] == ""
        assert parsed["name"] is None


class TestPluralizeUnit:
    def test_singular_quantity_stays_singular(self):
        assert _pluralize_unit("cup", 1.0) == "cup"

    def test_plural_quantity_gets_pluralized(self):
        assert _pluralize_unit("cup", 2.0) == "cups"

    def test_irregular_abbreviation_unit_stays_same(self):
        assert _pluralize_unit("tbsp", 3.0) == "tbsp"

    def test_lb_has_special_plural(self):
        assert _pluralize_unit("lb", 1.0) == "lb"
        assert _pluralize_unit("lb", 2.0) == "lbs"


class TestFormatStructuredQuantity:
    def test_with_unit_pluralizes(self):
        assert format_structured_quantity(2.0, "cup") == "2 cups"

    def test_singular_with_unit(self):
        assert format_structured_quantity(1.0, "cup") == "1 cup"

    def test_no_unit_just_formats_quantity(self):
        assert format_structured_quantity(1.5, None) == "1 1/2"


class TestScaleStructuredIngredient:
    def test_scales_and_renders(self):
        ing = {"quantity": 2.0, "unit": "cup", "name": "flour", "raw_text": "2 cups flour"}
        assert scale_structured_ingredient(ing, 2.0) == "4 cups flour"

    def test_no_quantity_falls_back_to_raw_text_unscaled(self):
        ing = {"quantity": None, "unit": None, "name": None, "raw_text": "Salt to taste"}
        assert scale_structured_ingredient(ing, 2.0) == "Salt to taste"

    def test_no_name_omits_trailing_space(self):
        ing = {"quantity": 1.0, "unit": "cup", "name": None, "raw_text": "1 cup"}
        assert scale_structured_ingredient(ing, 1.0) == "1 cup"

    def test_scale_structured_ingredients_list(self):
        ings = [
            {"quantity": 2.0, "unit": "cup", "name": "flour", "raw_text": "2 cups flour"},
            {"quantity": None, "unit": None, "name": None, "raw_text": "Salt to taste"},
        ]
        result = scale_structured_ingredients(ings, 0.5)
        assert result == ["1 cup flour", "Salt to taste"]


class TestScaleRecipeToServingsStructured:
    def test_scales_by_ratio(self):
        ings = [{"quantity": 4.0, "unit": "cup", "name": "broth", "raw_text": "4 cups broth"}]
        result = scale_recipe_to_servings_structured(ings, current_servings=2, target_servings=1)
        assert result == ["2 cups broth"]

    def test_zero_current_servings_treated_as_one(self):
        ings = [{"quantity": 1.0, "unit": "cup", "name": "broth", "raw_text": "1 cup broth"}]
        result = scale_recipe_to_servings_structured(ings, current_servings=0, target_servings=3)
        assert result == ["3 cups broth"]


class TestIsIngredientSectionHeader:
    def test_for_the_x_colon(self):
        assert is_ingredient_section_header("For the Crust:") is True
        assert is_ingredient_section_header("For the Filling:") is True

    def test_bare_label_colon(self):
        assert is_ingredient_section_header("Garnish:") is True
        assert is_ingredient_section_header("CRUST:") is True

    def test_real_ingredients_not_flagged(self):
        assert is_ingredient_section_header("1 3/4 cups flour") is False
        assert is_ingredient_section_header("7 tablespoons butter (cut into small flocks)") is False
        assert is_ingredient_section_header("Salt to taste") is False

    def test_quantity_leading_line_ending_in_colon_not_flagged(self):
        # A real (if oddly formatted) ingredient line shouldn't be mistaken
        # for a header just because it ends with ':' - the leading quantity
        # is what distinguishes it.
        assert is_ingredient_section_header("2 cups sugar:") is False

    def test_empty_and_none(self):
        assert is_ingredient_section_header("") is False
        assert is_ingredient_section_header(None) is False

    def test_no_trailing_colon_not_flagged(self):
        assert is_ingredient_section_header("For the Crust") is False
