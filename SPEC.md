# Sous Project Specification

## Overview
Sous is a self-hosted recipe management application that allows users to organize, search, and discover recipes from a large imported corpus. The application includes advanced features like ingredient pairing analysis based on co-occurrence statistics.

## Target Users
- Home cooks who want to organize their recipes digitally
- Food enthusiasts interested in exploring cuisines and ingredient combinations
- People who prefer self-hosted solutions for privacy and control
- Individuals looking to plan meals with flavor pairing insights

## MVP Feature Scope

### Core Functionality
- Recipe CRUD operations (browse, search, view, save, update, delete)
- Print view for recipes
- Single-recipe import from URL using schema.org JSON-LD parsing
- Ingredient-pairing model based on co-occurrence statistics

### Technical Requirements
- Self-hosted application
- No external dependencies beyond standard libraries (when possible)
- Docker packaging ready

## Tech Stack

### Backend
- Python 3.9+ with standard library modules
- SQLite for local data storage (no external DB required for MVP)

### Frontend
- HTML/CSS/JavaScript with no framework (minimal, self-contained)

### Reasoning
Python with standard library provides a simple, reliable solution that doesn't require external dependencies while still being powerful enough for this application's needs.

## Future Roadmap

### Phase 2: Meal Planning - DONE (see PROGRESS.md for full detail)
- Meal plans (name + target eat time) combining multiple recipes
- Companion-recipe suggestions using ingredient co-occurrence (normalized
  PMI-style, not raw counts - see PROGRESS.md for why that mattered),
  cuisine match, and difficulty/time complement
- Backward-scheduling of cooking steps from target eat time, per recipe,
  merged into one plan-wide timeline, with active-step conflicts between
  recipes flagged (not auto-resolved - see meal_planner.py module docstring
  for the honest scope of this heuristic)
- Also built as part of this phase: the ingredient-pairing model (originally
  scoped as MVP Core Functionality, but never actually persisted before this -
  it only ran as one-off throwaway scripts) is now a real `ingredient_pairs`
  table, queryable at runtime

### Phase 3: Advanced Features - PARTIALLY DONE (see PROGRESS.md)
- Embedding-based ingredient pairing models - DONE. Real embeddings via the
  user's own local Ollama box (nomic-embed-text), 8,568 ingredient vectors,
  wired into suggest_companions as a boost on top of (not a replacement
  for) the PMI-normalized co-occurrence scoring. Zero new cost, zero heavy
  new dependencies (stdlib only - cosine similarity in pure Python was fast
  enough at this scale, no numpy needed).
- Smart recipe recommendations - superseded by Phase 7 (flavor profiles)
  below.
- Integration with grocery shopping lists - **DONE, 2026-07-13** (user
  requested it after a Tandoor/Paprika feature-parity comparison flagged
  it as the highest-leverage gap). Built as in-app shopping lists
  (`shopping_list.py`), not integration with a third-party grocery
  service - generate a list from a recipe or a whole meal plan, with
  matching (same canonical ingredient name + unit) quantities merged
  into one line rather than duplicated. See PROGRESS.md.

### Phase 4: Import & Organization
- **Recipe categories**: a real, separate field from cuisine (the MIT batch's
  `cuisine` column is actually mislabeled RecipeCategory data from the
  original import - e.g. "Chicken", "Dessert" - this phase cleans that up
  by giving categories their own real field, backfilled from that existing
  data where sensible).
- **Paprika import**: `.paprikarecipes` files are a zip of gzip-compressed
  per-recipe JSON (fields: name, ingredients, directions, prep_time,
  cook_time, servings, categories, notes, rating, source_url, image_url) -
  a well-documented, tractable format. Paprika's own `categories` field
  feeds directly into the categories work above.
- **Generic recipe JSON import**: a bulk importer for anything already
  exporting standard schema.org-shaped recipe JSON, not just one URL at a
  time. Other specific apps (Tandoor, Mealie, etc.) deferred until there's
  an actual collection to migrate from - Tandoor's export format
  specifically has no clean public schema (Django `dumpdata` fixtures) and
  would need real reverse-engineering effort to support well.
- **Quick-add on empty search**: if a search turns up nothing, offer to add
  that recipe directly from the search results rather than requiring a
  separate trip to the import/create page.

### Phase 5: Personal Cooking Log
- **Recipe notes**: free-text, timestamped notes attached to a recipe (e.g.
  "used less salt, still great" or "kids didn't like the mushrooms").
- **Usage calendar**: log each time a recipe is actually cooked, with a
  view of cooking history over time - both a per-recipe log and a
  calendar-style view across the whole collection.

### Phase 6: Recipe Utility
- **Recipe scaling**: scale ingredient quantities by a factor or to a target
  serving count. Ingredients are stored as free-text strings ("2 cups
  flour"), not structured quantity/unit/item - scaling requires parsing
  quantities out of that text (fractions, unicode fraction glyphs, ranges,
  units) via heuristics, similar in spirit to the duration-extraction work
  in meal planning. Real accuracy limits will exist and will be documented
  honestly, not hidden.
- **Print view customization**: concrete toggles first (show/hide images,
  font size, include/exclude nutrition info, one-recipe-per-page vs.
  compact layout) rather than a full user-designed template
  editor - that's a much bigger sub-feature, worth revisiting only if the
  toggle version turns out not to be enough.

### Phase 7: Flavor Profiles
The biggest new piece - genuinely new modeling, not an extension of the
existing co-occurrence/embedding pairing work:
1. Define a flavor taxonomy: basic tastes (sweet, sour, salty, bitter,
   umami) plus aromatic/other categories (citrus, earthy, smoky, floral,
   pungent, spicy-heat, etc.).
2. Use an LLM (Claude or local Ollama) to tag each of the ~8,568 embedded
   ingredients against that taxonomy - a real one-time classification pass
   across the existing ingredient vocabulary, not derived from statistics.
3. Answer "what flavors are in this ingredient/dish/meal/cuisine" by
   aggregating those tags up through a recipe's ingredient list.
4. Build flavor-profile-level pairing stats (which flavor combinations are
   common, rare, or essentially never paired) by rolling the existing
   ingredient-pairing data up through the flavor tags.
5. Surface flavor data in suggestions and, eventually, as an aid for
   creating new recipes (e.g. "this dish leans sweet+umami with no acid -
   here's what commonly balances that").
- **Deferred, not now**: researching an existing public flavor-pairing/
  flavor-network dataset (academic food-pairing research has published
  data along these lines) as a way to validate or enrich the LLM-tagged
  taxonomy above. Worth revisiting once the LLM-tagged version exists and
  we can see where it feels thin.

### Phase 8: Delight
- **Easter eggs**: on-demand funny/mock recipes - e.g. riffing on a real
  recipe already in the user's collection in a comedic style. Good use for
  the Claude CLI backend (already authenticated, currently unused for
  anything) given this is creative generation, not the kind of structured
  multi-file engineering work the local model struggles with.

## Out of Scope

- User accounts and multi-user support
- Cloud synchronization or backup features
- Mobile app development (web app only)
- Complex recipe rating systems
- Advanced nutritional analysis
- Integration with external APIs for live cooking assistance

## Dataset Sources

Three sources, tagged per-recipe via the `license` column so commercially-restricted
data can always be filtered out later if needed:

### 1. AkashPS11/recipes_data_food.com (MIT) - 1,223 recipes
- https://huggingface.co/datasets/AkashPS11/recipes_data_food.com
- Claims ~1M rows, but 99.88% have NULL ingredients/instructions in this
  particular parquet mirror - only ~1,228 rows are actually usable. Verified
  directly (`pyarrow.compute.is_null` over the full ingredients/instructions
  columns) after the first import attempt suspiciously returned the same tiny
  count twice in a row.

### 2. Hieu-Pham/kaggle_food_recipes (MIT) - 13,495 recipes
- https://huggingface.co/datasets/Hieu-Pham/kaggle_food_recipes
- Verified 0% nulls across all columns before importing. Plain-text
  ingredients/instructions.

### 3. datahiveai/recipes-with-nutrition (CC BY-NC 4.0) - 39,447 recipes
- https://huggingface.co/datasets/datahiveai/recipes-with-nutrition
- Verified 0% nulls. Sourced from Food Network/Cookstr/Serious Eats/Food52/
  Allrecipes. Has real cuisine tags and full nutrition data (weight, calories,
  macros) - a bonus for future features.
- **No cooking instructions field at all** - this source is ingredients +
  nutrition only, not full step-by-step recipes. Fine for the ingredient-
  pairing model; these entries are incomplete for the recipe-view/print/cook
  flow (empty instructions).
- **License note**: CC BY-NC 4.0 is non-commercial only. Fine for personal
  self-hosted use; would block ever selling or publicly redistributing this
  app bundled with this data. The `license` column lets this be filtered out
  later if that ever matters.

**Total: 54,166 recipes** (54,165 from datasets + 1 from a real schema.org
URL import, verified against a live page).