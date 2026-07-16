"""Scaling of free-text recipe ingredients. The raw-text scale_ingredient()
family below remains a hand-rolled heuristic: parses only a leading
quantity; will not catch quantities mid-string, unusual unit
abbreviations, or unicode glyphs beyond the common fraction set listed
below. parse_ingredient() (the structured quantity/unit/name layer used
by RecipeDatabase) is backed by the ingredient-parser-nlp package as of
2026-07-12 - see its own docstring.
"""
import re
from fractions import Fraction

from ingredient_parser import parse_ingredient as _nlp_parse_ingredient

UNICODE_FRACTIONS = {
    '½': Fraction(1, 2),   # ½
    '¼': Fraction(1, 4),   # ¼
    '¾': Fraction(3, 4),   # ¾
    '⅓': Fraction(1, 3),   # ⅓
    '⅔': Fraction(2, 3),   # ⅔
    '⅛': Fraction(1, 8),   # ⅛
}

_UNICODE_FRAC_CLASS = ''.join(UNICODE_FRACTIONS.keys())

_DECIMAL_RE = re.compile(r'^\s*(?P<dec>\d+\.\d+)')
_MIXED_RE = re.compile(r'^\s*(?P<whole>\d+)\s+(?P<num>\d+)/(?P<denom>\d+)')
_SIMPLE_FRACTION_RE = re.compile(r'^\s*(?P<num>\d+)/(?P<denom>\d+)')
_WHOLE_PLUS_UNICODE_RE = re.compile(r'^\s*(?P<whole>\d+)(?P<frac>[' + _UNICODE_FRAC_CLASS + r'])')
_UNICODE_ALONE_RE = re.compile(r'^\s*(?P<frac>[' + _UNICODE_FRAC_CLASS + r'])')
_INTEGER_RE = re.compile(r'^\s*(?P<whole>\d+)(?:\s*-\s*\d+(?:\.\d+)?)?')  # "-4" of a "3-4" range, ignored


def parse_quantity(ingredient: str):
    for pattern in (_DECIMAL_RE, _MIXED_RE, _SIMPLE_FRACTION_RE,
                    _WHOLE_PLUS_UNICODE_RE, _UNICODE_ALONE_RE, _INTEGER_RE):
        match = pattern.match(ingredient)
        if not match:
            continue
        groups = match.groupdict()
        if pattern is _DECIMAL_RE:
            value = float(groups['dec'])
        else:
            total = Fraction(int(groups['whole'])) if groups.get('whole') else Fraction(0)
            if groups.get('num') and groups.get('denom'):
                total += Fraction(int(groups['num']), int(groups['denom']))
            elif groups.get('frac'):
                total += UNICODE_FRACTIONS[groups['frac']]
            value = float(total)
        remainder = ingredient[match.end():].lstrip()
        return value, remainder

    return None, ingredient


_COMMON_FRACTIONS = [
    (Fraction(1, 8), "1/8"),
    (Fraction(1, 4), "1/4"),
    (Fraction(1, 3), "1/3"),
    (Fraction(1, 2), "1/2"),
    (Fraction(2, 3), "2/3"),
    (Fraction(3, 4), "3/4"),
]


def format_quantity(value: float) -> str:
    if abs(value - round(value)) < 0.01:
        return str(int(round(value)))

    whole = int(value)
    remainder = value - whole

    for frac_value, frac_str in _COMMON_FRACTIONS:
        if abs(remainder - float(frac_value)) < 0.01:
            return f"{whole} {frac_str}" if whole else frac_str

    rounded = round(value, 2)
    text = f"{rounded:.2f}".rstrip('0').rstrip('.')
    return text


def scale_ingredient(ingredient: str, factor: float) -> str:
    quantity, remainder = parse_quantity(ingredient)
    if quantity is None:
        return ingredient
    scaled = quantity * factor
    return f"{format_quantity(scaled)} {remainder}"


def scale_ingredients(ingredients, factor: float):
    return [scale_ingredient(item, factor) for item in ingredients]


def scale_recipe_to_servings(ingredients, current_servings: int, target_servings: int):
    if current_servings <= 0:
        current_servings = 1
    factor = target_servings / current_servings
    return scale_ingredients(ingredients, factor)


# ---- Structured ingredient model ----
#
# The functions above treat each ingredient as an opaque string, re-parsing
# the leading quantity out of raw text on every scale request. The
# functions below persist that parse as structured (quantity, unit, name)
# fields (see RecipeDatabase's recipe_ingredients table) - same underlying
# heuristic (there's no ground-truth structured ingredient data in the
# source datasets to parse *from* instead), but computed once and stored,
# not re-derived on every request, and queryable by name/unit rather than
# only usable for display-string scaling.

# Canonical unit -> every text variant that should map to it. Deliberately
# not exhaustive (free-text recipe data has too long a tail of unit
# spellings to ever fully cover) - an unrecognized leading word just means
# unit=None and the whole remainder becomes `name`, which is always a safe
# fallback (never raises, never silently drops text).
_UNIT_CANONICAL = {
    'cup': ['cup', 'cups', 'c'],
    'tbsp': ['tablespoon', 'tablespoons', 'tbsp', 'tbsps', 'tbs'],
    'tsp': ['teaspoon', 'teaspoons', 'tsp', 'tsps'],
    'oz': ['ounce', 'ounces', 'oz'],
    'lb': ['pound', 'pounds', 'lb', 'lbs'],
    'g': ['gram', 'grams', 'g'],
    'kg': ['kilogram', 'kilograms', 'kg'],
    'ml': ['milliliter', 'milliliters', 'millilitre', 'millilitres', 'ml'],
    'l': ['liter', 'liters', 'litre', 'litres', 'l'],
    'pinch': ['pinch', 'pinches'],
    'dash': ['dash', 'dashes'],
    'clove': ['clove', 'cloves'],
    'can': ['can', 'cans'],
    'package': ['package', 'packages', 'pkg'],
    'slice': ['slice', 'slices'],
    'stick': ['stick', 'sticks'],
    'qt': ['quart', 'quarts', 'qt'],
    'pt': ['pint', 'pints', 'pt'],
    'gal': ['gallon', 'gallons', 'gal'],
    'bunch': ['bunch', 'bunches'],
    'sprig': ['sprig', 'sprigs'],
    'head': ['head', 'heads'],
    'jar': ['jar', 'jars'],
    'stalk': ['stalk', 'stalks'],
}
_UNIT_LOOKUP = {alias: canonical for canonical, aliases in _UNIT_CANONICAL.items() for alias in aliases}

_LEADING_WORD_RE = re.compile(r"^\s*([A-Za-z]+)\.?\s*(?:of\s+)?(.*)$", re.DOTALL)


def _parse_ingredient_heuristic(raw_text: str) -> dict:
    """Original regex/keyword heuristic: parse one free-text ingredient
    line into {quantity, unit, name, raw_text, preparation}. quantity/unit
    are None when nothing recognizable was found (e.g. "Salt to taste") -
    name then falls back to the full original text, never dropped.
    preparation is always None here - the heuristic has no notion of it,
    unlike the ML parser's labeled preparation span. Kept as the fallback
    parse_ingredient() uses if the ML-based parser raises on some input -
    see parse_ingredient()'s docstring."""
    quantity, remainder = parse_quantity(raw_text or '')
    remainder = (remainder or '').strip()

    unit = None
    name = remainder or None
    match = _LEADING_WORD_RE.match(remainder) if remainder else None
    if match:
        candidate, rest = match.group(1).lower(), match.group(2).strip()
        canonical = _UNIT_LOOKUP.get(candidate)
        if canonical and rest:
            unit = canonical
            name = rest

    return {'quantity': quantity, 'unit': unit, 'name': name, 'raw_text': raw_text, 'preparation': None}


def _ingredient_name_from_nlp_result(raw_text: str, result) -> str | None:
    """Recover the descriptive remainder (everything but the leading
    quantity+unit) from an ingredient_parser ParsedIngredient, preferring
    exact substring slicing of the original text (preserves punctuation/
    formatting exactly, matching this project's existing convention)
    over reassembling from labeled fragments, which loses commas and
    hyphens to tokenization. Substring slicing only fails when the
    quantity is inferred rather than literally present in the text (e.g.
    "1/4-inch piece of fresh ginger" implies quantity=1 "piece" with no
    literal "1" to find) - fragment reassembly is the fallback for that
    rare case."""
    if result.amount:
        first = result.amount[0]
        sub_amounts = getattr(first, 'amounts', None) or [first]
        amt_text = sub_amounts[0].text
        idx = raw_text.find(amt_text)
        if idx != -1:
            remainder = raw_text[idx + len(amt_text):]
            # amt_text excludes trailing punctuation the unit itself carried
            # in the original text (e.g. "1 Tbsp." -> amt_text "1 Tbsp",
            # leaving a stray "." here) - strip it along with a leading
            # comma from cases like "1 pound, chopped".
            remainder = remainder.strip().lstrip('.,').strip()
            if remainder:
                return remainder

    parts = []
    if result.size:
        parts.append((result.size.starting_index, result.size.text))
    for n in (result.name or []):
        parts.append((n.starting_index, n.text))
    if result.preparation:
        parts.append((result.preparation.starting_index, result.preparation.text))
    if result.comment:
        parts.append((result.comment.starting_index, result.comment.text))
    parts.sort(key=lambda p: p[0])
    return ' '.join(p[1] for p in parts) if parts else None


def is_ingredient_section_header(raw_text: str) -> bool:
    """Detects component-section labels that some source recipes embed as
    plain entries in their ingredients list (e.g. "For the Crust:",
    "Filling:", "To Serve:") instead of real ingredients - a real ingredient
    line essentially never ends with a bare colon, and this deliberately
    runs before the (expensive, ML-backed) parse_ingredient() rather than
    trying to infer it from that call's output, which was inconsistent on
    this kind of garbage input (sometimes extracting a garbage `name` from
    the label text, sometimes nothing at all).

    Checked against every distinct ':'-terminated line already in this
    project's corpus (198 rows across 108 recipes) with zero false
    positives - see backfill_section_headers.py."""
    text = (raw_text or '').strip()
    if not text.endswith(':'):
        return False
    quantity, _ = parse_quantity(text)
    return quantity is None


def parse_ingredient(raw_text: str) -> dict:
    """Parse one free-text ingredient line into structured fields:
    {quantity, unit, name, raw_text, confidence, preparation}. quantity/unit
    are None when nothing recognizable was found (e.g. "Salt to taste") - name
    then falls back to the full original text, never dropped (name still
    includes any preparation language, e.g. "diced onion", not just "onion" -
    preparation is additive, not a rewrite of name's existing meaning, so
    nothing that already reads recipe_ingredients.name breaks). confidence is
    the ML parser's own per-field confidence (averaged across whichever of
    amount/name were actually detected), None when nothing was detected or
    when the regex-heuristic fallback ran instead (it has no comparable
    notion of confidence). preparation is the ML parser's own labeled
    preparation span (e.g. "diced", "finely chopped") when it found one,
    else None - always None when the regex-heuristic fallback ran, since it
    has no notion of preparation either.

    Backed by the ingredient-parser-nlp package (sequence-labeling model,
    95.6%/98.3% sentence/word accuracy on its own test set) rather than a
    hand-rolled regex - verified against 400 real lines from this
    project's corpus at 98.5% exact agreement with the prior heuristic,
    with the disagreements being real heuristic bugs (e.g. compound
    quantities like "2 tablespoons plus 1/4 cup sugar" only partially
    captured; "1/4-inch piece of fresh ginger" misread as quantity=0.25).
    Only the *leading* quantity is used for compound amounts, matching
    this module's documented scaling scope - not a regression from the
    prior heuristic, which had the same limit.

    Falls back to the regex heuristic on any exception from the ML parser,
    or on any unexpected output shape from it (e.g. an IngredientAmount
    with quantity='' rather than a number or None, seen on "garlic
    cloves" - the model detected "cloves" as a unit-like word with no
    accompanying number rather than reporting no amount at all) - recipe
    saves must never fail because of a parsing library quirk. See
    _parse_ingredient_heuristic()."""
    if not (raw_text or '').strip():
        return {'quantity': None, 'unit': None, 'name': None, 'raw_text': raw_text, 'confidence': None, 'preparation': None}

    try:
        result = _nlp_parse_ingredient(raw_text)

        quantity = None
        unit = None
        confidences = []
        if result.amount:
            first = result.amount[0]
            sub_amounts = getattr(first, 'amounts', None) or [first]
            raw_qty = sub_amounts[0].quantity
            quantity = float(raw_qty) if isinstance(raw_qty, (int, float, Fraction)) else None
            raw_unit = sub_amounts[0].unit
            if raw_unit:
                unit = _UNIT_LOOKUP.get(str(raw_unit).lower(), str(raw_unit))
            confidences.append(sub_amounts[0].confidence)

        name = _ingredient_name_from_nlp_result(raw_text, result)
        if result.name:
            confidences.append(result.name[0].confidence)
        confidence = sum(confidences) / len(confidences) if confidences else None
        preparation = result.preparation.text if result.preparation else None

        return {
            'quantity': quantity, 'unit': unit, 'name': name, 'raw_text': raw_text,
            'confidence': confidence, 'preparation': preparation,
        }
    except Exception:
        parsed = _parse_ingredient_heuristic(raw_text)
        parsed['confidence'] = None
        return parsed


# Units whose plural isn't a plain trailing 's'. Abbreviation-style units
# (tbsp, oz, g, ...) conventionally stay the same in both singular and
# plural in recipe writing ("2 tbsp", not "2 tbsps") - everything else in
# _UNIT_CANONICAL pluralizes fine with a plain trailing 's' (cup/cups,
# clove/cloves, can/cans, etc).
_IRREGULAR_PLURALS = {u: u for u in ('tbsp', 'tsp', 'oz', 'g', 'kg', 'ml', 'l', 'qt', 'pt', 'gal')}
_IRREGULAR_PLURALS['lb'] = 'lbs'


def _pluralize_unit(unit: str, quantity: float) -> str:
    if abs(quantity - 1) < 0.01:
        return unit
    return _IRREGULAR_PLURALS.get(unit, f'{unit}s')


def format_structured_quantity(quantity, unit) -> str:
    """Render a scaled (quantity, unit) pair back to display text, e.g.
    (1.5, 'cup') -> '1 1/2 cups' (pluralized when the quantity isn't 1)."""
    text = format_quantity(quantity)
    return f"{text} {_pluralize_unit(unit, quantity)}" if unit else text


def scale_structured_ingredient(ingredient: dict, factor: float) -> str:
    """Scale one structured ingredient dict (as returned by parse_ingredient
    or RecipeDatabase.get_structured_ingredients) back to a display string.
    Falls back to the raw text unscaled if it has no parsed quantity."""
    if ingredient.get('quantity') is None:
        return ingredient['raw_text']
    scaled_qty = ingredient['quantity'] * factor
    prefix = format_structured_quantity(scaled_qty, ingredient.get('unit'))
    name = ingredient.get('name')
    return f"{prefix} {name}" if name else prefix


def scale_structured_ingredients(ingredients, factor: float):
    return [scale_structured_ingredient(ing, factor) for ing in ingredients]


def scale_recipe_to_servings_structured(structured_ingredients, current_servings: int, target_servings: int):
    """Structured-data equivalent of scale_recipe_to_servings() - takes
    RecipeDatabase.get_structured_ingredients() output instead of raw
    ingredient strings, avoiding a live re-parse of each ingredient on
    every scale request."""
    if current_servings <= 0:
        current_servings = 1
    factor = target_servings / current_servings
    return scale_structured_ingredients(structured_ingredients, factor)
