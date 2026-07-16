# Architecture

Technical reference for how Sous is built. For what it does and how to run
it, see [README.md](README.md). For the dated history of every phase,
decision, and verification, see [PROGRESS.md](PROGRESS.md) - this document
is a snapshot of current structure, not a changelog.

## Design principles

- **Standard library first, dependencies added when justified.** Most of
  the app runs on Python's stdlib alone: `http.server` for the web layer,
  `sqlite3` for storage, `urllib` for outbound HTTP (URL import). No ORM,
  no web framework. `litellm` is an accepted exception - every LLM-backed
  feature goes through it via `llm_client.py` as of 2026-07-16 (PLAN.md
  Phase 17), no longer just the "Comedic riff" easter egg. Pure Python has stayed
  fast enough for the ingredient-pairing and embedding-similarity work
  done so far at current data sizes (54K recipes, ~1M ingredient pairs),
  but numpy is no longer excluded on principle - as of 2026-07-12, the
  no-numpy rule was lifted (see PROGRESS.md). Adding it should still be
  justified by an actual need, not reached for by default.
- **SQLite as the only datastore**, one file (`recipes.db`), no server
  process. Every module connects with its own short-lived
  `sqlite3.connect()` call rather than a shared connection pool - simple,
  correct for this access pattern (a single-process dev server), and
  consistent everywhere in the codebase.
- **Heuristics are documented as heuristics.** Recipe scaling
  (`recipe_scaling.py`), cooking-step duration/scheduling
  (`meal_planner.py`), and ingredient-pairing normalization all rely on
  regex/keyword heuristics over free-text data - there's no ground-truth
  structured data in the source datasets to parse from instead. Every one
  of these modules' docstrings says so explicitly - this was a deliberate
  project-wide convention, not an oversight per module. As of 2026-07-10,
  scaling and step-timing extraction are computed once and persisted as
  real structured data (`recipe_ingredients`, `recipe_steps` - see below)
  rather than re-derived on every request, but the underlying parse is
  still the same heuristic.
- **Automated tests are targeted at the highest-risk/highest-complexity
  business logic, not exhaustive.** `tests/` (pytest) covers shopping-list
  quantity merging, pantry shelf-life decay/confirmation,
  backward-scheduling conflict detection, ingredient quantity parsing/
  scaling, and the flavor-aggregation/discovery queries - see "Testing"
  below. Most other features are still
  verified by hand against a live running server instance and direct SQL
  queries during development (see PROGRESS.md for the specifics of each
  verification) - deliberately so for anything that depends on the LLM
  or the live corpus, where a real check against real data/a real model
  response catches things a mock would paper over.

## Testing

`tests/` (pytest, listed as a dev-only dependency in `requirements.txt`,
not installed by `docker compose up` or native `python3 server.py`).
`tests/conftest.py` gives every test its own throwaway SQLite file
(pytest's `tmp_path`), with schema created the same way the app itself
creates it - `RecipeDatabase.__init__`/`MealPlanDatabase.__init__`/each
feature module's own `init_*_table()` - rather than hand-rolled `CREATE
TABLE` statements that could drift from the real schema unnoticed.
`make_recipe()`/`set_ingredient_quantities()` build test recipes through
the real `save_recipe()` path (so FTS/structured-ingredient sync all run
for real) and then optionally overwrite the parsed ingredient
quantities/units directly, decoupling "does the merge/scheduling logic
work" from "is the ML ingredient parser accurate" (already covered
separately - see PROGRESS.md's 2026-07-12 entries on adopting
`ingredient-parser-nlp`).

- **`tests/test_shopping_list.py`** - quantity-merging by matching
  `(name, unit)`, cross-unit non-merging, checked items never being
  merge targets, and a direct regression test for a real bug this
  session (merging into an existing NULL-quantity row raised a
  `TypeError` before the merge query required the existing row's
  quantity to be non-null).
- **`tests/test_pantry.py`** - the 0.8x/1.5x shelf-life thresholds
  (`fresh`/`needs_confirmation`/auto-discarded) via backdated synthetic
  rows, `get_confirmed_fresh_names()` actually excluding
  `needs_confirmation` items (the exact guarantee `/discover`'s pantry
  auto-fill relies on), and `add_or_refresh_item()` deduping rather than
  duplicating.
- **`tests/test_scheduling.py`** - `extract_step_duration`/
  `classify_step_type` against real free text, and the conflict
  detector: overlapping active steps flagged, overlapping passive steps
  not, non-overlapping active steps not, and a recipe added twice to the
  same plan never "conflicting with itself."
- **`tests/test_recipe_scaling.py`** - the raw-text and structured-
  ingredient quantity parser/formatter/scaler (`recipe_scaling.py`),
  covering unicode fractions, mixed numbers, ranges, decimal quantities,
  unit-less lines, and irregular unit pluralization - these are pure
  functions with no DB dependency.
- **`tests/test_flavor_queries.py`** - ingredient/recipe/cuisine/
  meal-plan flavor profile aggregation (`flavor_queries.py`), including
  a direct regression test pinning the 70bbde8 fix (profiles must key
  off `recipe_ingredients.name`, the canonical parsed name, not raw
  ingredient text).
- **`tests/test_recipe_flavor_index.py`** - the `recipe_flavors`
  precomputed index build step and `find_recipes_by_flavors()`'s
  ranking, cuisine/max-time filters, and its requirement that a matched
  recipe actually have instructions.

## Module map

### Web layer
- **`server.py`** (~3,550 lines) - the entire HTTP layer.
  `http.server.SimpleHTTPRequestHandler` subclass (`RecipeHandler`) with
  hand-rolled routing in `do_GET`/`do_POST`/`do_PUT`/`do_DELETE`/
  `do_OPTIONS` (no framework - a big `if/elif` chain on `self.path`,
  with a `path.startswith('/api/v1/')` check ahead of it for the public
  API - see "Public API" below). Every page is a Python f-string
  returning raw HTML; `get_base_style()`/`get_nav_html()` are the only
  shared rendering helpers. See "API routes" below for the full list.
  `get_nav_html()` groups its non-primary links into four
  `<details>/<summary>` dropdowns (Add, Discover, Plan, You) rather
  than one flat list - `<details>` is natively keyboard-accessible and
  needs no JS to function; a small inline `<script>` just adds
  "only one open at a time" / "closes on outside click" polish on top.
  Top-level (not in a dropdown): Home, Search, Categories - Categories
  moved out of the Discover dropdown 2026-07-16 after user feedback that
  it needed to be easier to reach. Discover's two LLM-backed entries
  (Craving?, Invent) carry a small `.llm-badge` ("LLM") so it's visible
  at a glance which Discover items call a model and which are pure SQL
  (What Can I Make?, Pairings aren't) - same badge repeated on each of
  those two pages themselves, with a sentence explaining what gets sent.

### Data model
- **`recipe_model.py`** - `Recipe` (plain data class) and
  `RecipeDatabase` (CRUD against the `recipes` table). Owns
  `init_database()`, which also runs additive schema migrations
  (`ALTER TABLE ... ADD COLUMN` guarded by a `PRAGMA table_info` check) -
  this is how `license`, then `image_url`/`nutrition`, were added without
  a separate migration tool. `get_recipe_of_the_day()` (renamed from
  `get_random_recipe_with_image()` 2026-07-16) picks the home page hero
  deterministically from today's UTC date (`sha256(date.isoformat()) %
  count`, then `ORDER BY id LIMIT 1 OFFSET`) instead of
  `ORDER BY RANDOM()` - the old version re-picked on every page load
  despite being labeled "today's pick," a real reported bug.

### Import
- **`import_url_recipe.py`** - single-URL import via schema.org
  `Recipe` JSON-LD parsing (`extract_json_ld_scripts`,
  `extract_recipe_data`).
- **`import_paprika.py`** - Paprika `.paprikarecipes` import. Format is a
  zip archive of gzip-compressed per-recipe JSON entries. Also captures
  each recipe's photo via `recipe_images.add_image_upload()`/
  `add_image_url()` - `photo_data` (base64, Paprika's locally-stored
  photo) preferred over `image_url` (the original web image link) when
  both are present, since it doesn't rot like an external link.
- **`import_bulk.py`** - generic bulk import, reuses
  `import_url_recipe.extract_recipe_data()` against a JSON file
  containing one or many schema.org-shaped recipe objects.
- **`import_real_data.py`** - the original one-off dataset-import script
  (AkashPS11/Hieu-Pham/datahiveai batches that seeded the 54,167-recipe
  corpus). Not used at runtime; kept for provenance/re-import reference.
- **`backfill_nutrition.py`** - one-off backfill of `image_url`/
  `nutrition` from the AkashPS11 and datahiveai source datasets. Not used
  at runtime after having been run once against production.
- **`backfill_paprika_photos.py`** - one-off recovery of photos for the
  555 recipes imported before `import_paprika.py` captured them (see
  PROGRESS.md 2026-07-16). Matches the original export file back to
  already-imported rows by exact `(title, url)`. Not used at runtime.

### Categories, notes, cooking log
- **`categories.py`** - `recipe_categories` junction table (many-to-many,
  a recipe can have multiple categories). `add_category`,
  `get_categories`, `get_recipes_by_category`.
- **`cooking_log.py`** - `recipe_notes` and `cook_log` tables. Free-text
  timestamped notes and cook-date entries, both keyed by `recipe_id`.

### Meal planning
- **`meal_planner.py`** (largest module besides `server.py`, ~600 lines) -
  `MealPlanDatabase`: meal plan CRUD, `suggest_companions()` (the
  recommendation engine - PMI-normalized co-occurrence + embedding boost +
  cuisine/time signals, see its own docstring), `rebuild_ingredient_pairs()`
  (builds `ingredient_pairs`/`ingredient_totals` from every recipe's
  ingredient list), and the backward-scheduling functions
  (`extract_step_duration`, `classify_step_type`,
  `backward_schedule_recipe`, `backward_schedule_plan`).
- **`shopping_list.py`** - `shopping_lists`/`shopping_list_items` CRUD,
  plus `add_recipe_to_list()`/`add_plan_to_list()` which pull a recipe's
  (or every recipe in a plan's) `get_structured_ingredients()` output and
  merge each into an existing unchecked line via `_merge_or_insert()`
  when it shares the same canonical `(name, unit)` - matching quantities
  are summed, not duplicated. Ingredients with no parsed quantity, or
  whose unit doesn't match an existing line, are never merged, only
  added as their own line - no unit-conversion table exists in this
  project, so cross-unit merging isn't attempted.
- **`pantry_shelf_life.py`** - mirrors `flavor_tagging.py`'s structure
  exactly: a fixed taxonomy (`highly_perishable`/`perishable`/
  `semi_perishable`/`frozen`/`shelf_stable`, each with a representative
  day count), batched LLM classification against the same
  `ingredient_embeddings` candidate pool, cached in
  `ingredient_shelf_life` and resumable via
  `ingredient_shelf_life_tagged`. `get_shelf_life()` falls back to
  `semi_perishable` (30 days) for anything never classified - a
  deliberately moderate default, not "lasts forever."
- **`pantry.py`** - `pantry_items` CRUD, plus the decay/confirmation
  logic that makes this more than a flat inventory list:
  `discard_expired_items()` removes anything past 1.5x its shelf life,
  `get_items()` computes each remaining item's status
  (`fresh`/`needs_confirmation`, at the 0.8x/1.5x-of-shelf-life
  thresholds) fresh on every read rather than storing a status column
  that could itself go stale, and `get_confirmed_fresh_names()` -
  what `/discover`'s pantry auto-fill uses - excludes
  `needs_confirmation` items entirely, so the app never assumes stale
  stock is still there. `add_or_refresh_item()` resets an existing
  item's clock instead of creating a duplicate row, which is what makes
  checking off a shopping-list item a reasonable "I just restocked this"
  signal (wired in `server.py`'s shopping-list-item-toggle handlers).

### LLM provider
- **`llm_client.py`** (added 2026-07-16, PLAN.md Phase 17) - the one
  place every LLM-backed feature talks to a model, backed by `litellm`.
  `chat()`/`chat_json()` for completions (`json_mode`/`chat_json` uses
  litellm's `response_format={"type": "json_object"}`, translated
  per-provider - Ollama's own `format: "json"` underneath), `embed()`
  for embeddings (Ollama-only regardless of the active chat provider -
  `ingredient_embeddings.vector`'s dimensionality is tied to the specific
  embedding model already used corpus-wide, so switching would need a
  full re-embed of ~7,200 ingredients). `get_active_provider_and_model()`
  resolves provider/model in order: the saved preference
  (`preferences.py`'s `llm_provider`/`llm_model`, UI-configurable on
  `/preferences`, takes effect immediately) -> `SOUS_LLM_PROVIDER`/
  `SOUS_LLM_MODEL` env vars -> the original Ollama/`qwen3:8b` default.
  `_resolve_api_key()` similarly prefers a key saved via `llm_credentials.py`
  over the provider's standard env var (`ANTHROPIC_API_KEY`,
  `GEMINI_API_KEY`), passed explicitly as `litellm.completion(api_key=...)`
  rather than relying on litellm's own env-var auto-read, since the
  UI-stored key needs to win when both are set. `get_ollama_host()` is
  the same three-tier pattern applied to Ollama's connection detail
  (it has no API key, but does need a reachable address): saved
  `ollama_host` preference -> `OLLAMA_HOST` env var -> a hardcoded LAN
  default - added same-day as a follow-up once initial user feedback
  pointed out that provider *selection* without being able to configure
  each provider's actual connection details (host for Ollama, key for
  the others) didn't really solve anything. `test_connection(provider,
  model, api_key, ollama_host)` (also added same-day) takes explicit
  values rather than resolving the saved config, so the Preferences
  page's "Test Connection" button validates whatever's currently in the
  form - saved or not - before committing to it; sends one minimal real
  completion request and returns `(ok, message)`, never raises. Replaced 7 previously-independent
  `urllib.request`-to-`OLLAMA_HOST` call sites (`embeddings.py`,
  `flavor_tagging.py`, `query_planner.py`, `pantry_shelf_life.py`,
  `recipe_adaptation.py`, `recipe_invention.py`, `easter_egg.py`'s own
  separate `litellm` usage) - motivated by a real same-day outage where
  the LAN Ollama host went unreachable and (combined with the
  single-threaded server, also fixed same day) took the entire app
  offline, not just the LLM features. See PROGRESS.md/MISSING_FEATURES.md.
- **`llm_credentials.py`** (added 2026-07-16) - `llm_credentials.db`, a
  *separate* SQLite file from `recipes.db`, gitignored (like `uploads/`)
  for the same reason `llm_client.py`'s docstring gives: `recipes.db` is
  git-tracked in this project, so a stored secret there would leak into
  history the moment anyone commits. One table, `(provider PRIMARY KEY,
  api_key)`. `save_api_key(provider, '')` deletes rather than storing an
  empty string, so the Preferences form's "leave blank to keep whatever's
  saved" semantics don't need a separate "no change" sentinel - blank
  from the client always means "don't touch what's already there"
  (`handle_save_preferences()` only calls this when `llm_api_key` is
  non-empty). Needs a real *file* (not directory) bind-mounted at this
  path under Docker before first start - see docker-compose.yml.

### Embeddings and flavor
- **`embeddings.py`** - `get_embedding()` (thin wrapper over
  `llm_client.embed()`), `cosine_similarity()`, `build_ingredient_embeddings()`
  (batch-embeds `ingredient_embeddings`).
- **`flavor_taxonomy.py`** - the 17-category taxonomy (5 basic tastes + 12
  aromatic) as a plain tuple, plus `flavor_categories` table
  seed/query functions.
- **`flavor_tagging.py`** - LLM-tags every embedded ingredient against the
  taxonomy via the active provider (`llm_client.chat_json()` - Ollama's
  `think=False` flag still applied when that's the active provider, see
  `llm_client._model_string_and_kwargs()`). Populates `ingredient_flavors` +
  `ingredient_flavor_tagged`.
- **`flavor_queries.py`** - read-only aggregation:
  `get_ingredient_flavor_profile`, `get_recipe_flavor_profile`,
  `get_cuisine_flavor_profile`, `get_meal_plan_flavor_profile`.
- **`flavor_pairing.py`** - rolls `ingredient_pairs` up through
  `ingredient_flavors` into `flavor_pair_stats` (which flavor
  combinations are common/rare/never-observed).
- **`recipe_flavor_index.py`** - `build_recipe_flavor_index()` precomputes
  `recipe_flavors(recipe_id, flavor, weight)` from `recipe_ingredients` +
  `ingredient_flavors` via one indexed join, and `find_recipes_by_flavors()`
  queries it. Exists so `/craving` doesn't have to re-derive each
  candidate recipe's flavor profile in Python per request across the
  whole corpus - see [Technical Report No. 1](docs/papers/01-flavor-pairing-engine.md).

### Recipe discovery
- **`query_planner.py`** - `plan_intent_query()` sends free text to the
  active LLM provider (`llm_client.chat_json()`) with a fixed prompt
  listing the 17 valid flavor categories, and asks for four closed/
  semi-closed fields: `flavors`, `cuisine`, `max_total_time_minutes`,
  `keywords`. Any flavor name outside the fixed vocabulary is silently
  dropped rather than passed through. Falls back to a keyword-only plan
  (`{'flavors': [], 'keywords': [user_text]}`) on any failure (timeout,
  provider unreachable, bad JSON), so callers never have to special-case
  a model outage. Backs `/craving` (server.py) and, when no explicit seed
  ingredients are given, `recipe_invention.py`'s mood-to-seeds path. See
  [Technical Report No. 2](docs/papers/02-recipe-discovery-engine.md).

### Recipe utility
- **`recipe_scaling.py`** - free-text ingredient quantity parsing
  (decimals, simple/mixed fractions, unicode fraction glyphs) and
  scaling by factor or target servings. Only parses a *leading* quantity
  per ingredient line - see its own docstring for the exact scope. Also
  has a structured-ingredient layer (`parse_ingredient()` ->
  `{quantity, unit, name, raw_text}`, plus `scale_recipe_to_servings_structured()`)
  that `RecipeDatabase` uses to populate/keep in sync the
  `recipe_ingredients` table, so scaling reads persisted structured data
  instead of re-parsing raw text on every request.
- **`recipe_images.py`** - `recipe_images` table (any number of photos
  per recipe, each either an external `url` or a local `filename`).
  `recipes.image_url` stays in sync as a denormalized "first image" cache
  (`_sync_primary_image()`) so every existing thumbnail call site
  (`recipe_thumb_html`, search results, home page) keeps working
  unchanged - this module is additive, not a replacement. One-time
  backfill on init promotes every recipe's pre-existing single
  `image_url` into a first `recipe_images` row. Because `image_url` is
  literally row 0 of this table, `server.py`'s recipe detail page
  renders the gallery thumbnail strip from row 1 onward
  (`gallery_images[1:]`), not the full list - rendering row 0 again
  there was a real reported bug (the same photo shown twice: big banner
  + a redundant small thumbnail underneath), fixed 2026-07-16.
- **`uploads.py`** - local file storage for uploaded photos. The one
  narrow, explicit exception to this app serving zero static files (see
  "Known architectural issues" below): filenames are always server-
  generated (`uuid4().hex` + a whitelisted extension, never derived from
  client input, so path traversal is structurally impossible rather than
  merely filtered), file type is validated by real magic-byte content
  sniffing (not a trusted client-supplied filename/Content-Type), and
  there's a 10MB size cap.

### Preferences and generation
- **`preferences.py`** - `preferences` table, one singleton row (id=1) -
  the app has no multi-user/auth concept anywhere, so this is a
  household-level record, not per-user. `get_preferences()`/
  `save_preferences()`; `recipe_conflicts_with_preferences()` checks a
  recipe's canonical ingredients against the disliked list.
  `hide_builtin_recipes` (bool) drives every browsing surface's
  `exclude_builtin` filter (`recipe_model.py`'s query methods,
  `categories.get_recipes_by_category()`/`get_category_counts()`) -
  `save_preferences()` preserves the current value when omitted rather
  than defaulting it to off, since the v1 API's `_api_update_preferences`
  saves the other three fields without knowing about this one.
- **`recipe_adaptation.py`** - `suggest_substitutions()` (embedding
  nearest-neighbors for disliked ingredients, filtered through a
  plural-aware stemmer - `top_embedding_similar_ingredients()`'s own
  substring-dedup doesn't catch e.g. "blueberries" vs. "blueberry") and
  `adapt_recipe_to_preferences()` (sends the full original recipe +
  preferences to the active LLM provider via `llm_client.chat_json()`,
  asks for the smallest set of changes that satisfy them; returns
  `None`, not an exception, if there's nothing to adapt or the provider
  is unreachable).
- **`recipe_invention.py`** - `build_ingredient_palette()` expands seed
  ingredients into a candidate list via real `ingredient_pairs`
  co-occurrence (not free association), excluding disliked ingredients
  at retrieval time. `invent_recipe()` sends that palette + preferences
  to the active LLM provider; if no seeds are given but a mood is,
  `_seed_ingredients_from_mood()` derives seeds via `query_planner`'s
  flavor extraction plus the most corpus-frequent ingredient per flavor.
  See [Technical Report No. 4](docs/papers/04-recipe-adaptation-invention-engine.md)
  for a direct contrast with `easter_egg.py` below (grounded vs.
  ungrounded generation) and a corrected hypothesis about ingredient
  rarity and grounding quality.

### Delight
- **`easter_egg.py`** - `generate_easter_egg_recipe()`, via
  `llm_client.chat()`. Used to have its own independent
  `SOUS_EASTER_EGG_MODEL` env var and lazy `litellm` import, separate
  from every other LLM feature's `OLLAMA_HOST` - unified onto the same
  shared provider/model as everything else 2026-07-16 (PLAN.md Phase
  17); `litellm` is no longer lazily imported anywhere; it's a hard
  requirement now that 6 of 7 LLM-backed features depend on it via
  `llm_client.py`. Deliberately ungrounded (no retrieval step) since its
  goal is comedy, not a usable recipe - contrast with
  `recipe_invention.py` above.

### Public API authentication
- **`api_keys.py`** - `api_keys` table (`key_hash`, `label`, timestamps,
  `revoked`). Keys are generated with `secrets.token_urlsafe`, stored only
  as a SHA-256 hash (`create_api_key`/`verify_api_key`/`list_api_keys`/
  `revoke_api_key`) - the raw key is returned once at creation and never
  persisted, so a lost key can only be revoked and reissued, not
  recovered.
- **`manage_api_keys.py`** - CLI (`create`/`list`/`revoke`) for the above.
  Deliberately not an HTTP endpoint: the app has no authenticated admin
  session anywhere to gate a "create key" route behind, so key management
  stays server-side-only.

## Database schema

Single file, `recipes.db`. All tables, as of the current schema:

| Table | Rows (current, 2026-07-10) | Purpose |
|---|---|---|
| `recipes` | 54,722 | Core recipe data. See below for full column list. |
| `recipe_categories` | 2,175 | `(recipe_id, category)` junction, many-to-many. |
| `recipe_notes` | 0 (empty until used) | Free-text timestamped notes per recipe. |
| `cook_log` | 0 (empty until used) | Cook-date entries per recipe. |
| `recipe_steps` | 113,945 | Structured cooking steps (`duration_minutes`, `step_type`) per instruction line, eagerly backfilled for every recipe that has instructions - previously only lazily cached per-recipe on first schedule request. Row count grew from earlier snapshots after the 2026-07-12 RecipeNLG instructions backfill gave many more recipes instructions to have steps from. Populated/read by `meal_planner.py`. 68.1% of these rows have no extractable duration and fall back to a 5-minute default - see [Technical Report No. 3](docs/papers/03-time-planning-engine.md). |
| `recipe_ingredients` | 517,392 | Structured `(quantity, unit, name, confidence, is_section_header)` per ingredient line, parsed via `recipe_scaling.parse_ingredient()` (now `ingredient-parser-nlp`-backed, not the original regex heuristic - see PROGRESS.md's 2026-07-12 entries) and backfilled for all recipes. `name` is the canonical key every co-occurrence/embedding/flavor table below is keyed against - joining against raw `recipes.ingredients` text instead is a recurring bug class this project hit three separate times (see PROGRESS.md). `is_section_header` (202 rows) flags component-section labels some source recipes embed as plain ingredient-list entries ("For the Crust:") rather than real ingredients - see `recipe_scaling.is_ingredient_section_header()` and PROGRESS.md's 2026-07-16 entry; `name`/`quantity`/`unit`/`confidence`/`preparation` are NULL on these rows by construction, so every existing `name`-keyed join/filter already excludes them for free. Read by the recipe-page scaling form, which renders these rows as headings instead of bullets (`server.py`'s `ingredients_list_html()`). |
| `recipes_fts` | 54,722 | FTS5 full-text index (title/description/ingredients, `bm25`-ranked) backing `search_recipes()`. Not a contentless table - duplicates the indexed text rather than referencing `recipes`. |
| `meal_plans` | 0 (empty until used) | `(name, target_eat_time)`. |
| `meal_plan_items` | 0 (empty until used) | `(meal_plan_id, recipe_id)` junction. |
| `ingredient_pairs` | 975,688 | `(ingredient_a, ingredient_b, pair_count)` co-occurrence, rebuilt by `rebuild_ingredient_pairs()`, keyed off canonical `recipe_ingredients.name` (not raw text - see the 2026-07-12 rekey in PROGRESS.md). |
| `ingredient_totals` | 58,300 | Per-ingredient occurrence count, used for the confidence-style normalization in `suggest_companions()` (see [Technical Report No. 1](docs/papers/01-flavor-pairing-engine.md) for why this is association-rule confidence, not true PMI, despite the in-code comment). |
| `ingredient_embeddings` | 7,194 | `(ingredient, vector)` - only ingredients with `total_count >= 3`. |
| `flavor_categories` | 17 | The taxonomy: `(name, category_group, description)`. |
| `ingredient_flavors` | 23,564 | `(ingredient, flavor)` tags, many-to-many. |
| `ingredient_flavor_tagged` | 11,997 | Marker table - which ingredients have been LLM-tagged (resumability). |
| `flavor_pair_stats` | 153 | `(flavor_a, flavor_b, pair_count)` rollup - the full C(17,2)+17 theoretical max. |
| `recipe_flavors` | 455,177 | `(recipe_id, flavor, weight)` precomputed index backing `/craving` - see `recipe_flavor_index.py` above. |
| `preferences` | 1 (singleton) | `(dietary_restrictions, disliked_ingredients, notes, hide_builtin_recipes, llm_provider, llm_model, ollama_host)` - one household-level row, no per-user records since there's no auth/multi-user concept anywhere in the app. `llm_provider`/`llm_model`/`ollama_host` are a provider id, model name, and URL - never a secret (that's `llm_credentials.db` instead) - see `llm_client.py`. |
| `api_keys` | grows with usage | `(key_hash, label, created_at, last_used_at, revoked)` for the public API (`/api/v1/*`). Raw keys are never stored - see `api_keys.py`. |
| `shopping_lists` | 0 (empty until used) | `(name, created_at, updated_at)`. Supports multiple named lists, same pattern as `meal_plans`. |
| `shopping_list_items` | 0 (empty until used) | `(list_id, name, quantity, unit, checked, source_recipe_id, position)`. `quantity`/`unit` are nullable (manual items, or ingredients whose parse failed). See `shopping_list.py`. |
| `recipe_images` | 39,563 | `(recipe_id, url, filename, position)` - exactly one of `url`/`filename` set per row. Backfilled 1:1 from every recipe's pre-existing `image_url` on introduction (39,563 recipes had one). See `recipe_images.py`. |
| `ingredient_shelf_life` | ~7,194 (matches `ingredient_embeddings`, tagging run to completion) | `(ingredient, category, days)` - one of the 5 `SHELF_LIFE_TAXONOMY` categories per ingredient. See `pantry_shelf_life.py`. |
| `ingredient_shelf_life_tagged` | same as above | Marker table - which ingredients have been LLM-tagged (resumability), same pattern as `ingredient_flavor_tagged`. |
| `pantry_items` | 0 (empty until used) | `(name, quantity, added_at, source)`. `source` is `'manual'` or `'shopping_list'` (informational only). No status column - freshness is computed from `added_at` + `ingredient_shelf_life` at read time, not stored. See `pantry.py`. |

Row counts above are current as of 2026-07-13. `ingredient_pairs`/
`ingredient_totals`/`ingredient_embeddings`/`ingredient_flavors` counts
moved from earlier snapshots of this doc as a direct result of the
2026-07-12 canonical-name rekey (raw-text keys collapsed/expanded into
fewer, correct canonical ones) and completing the flavor-tagging backlog
- not unexplained drift, unlike the note this replaced.

`recipes` columns: `id, title, description, ingredients (JSON list),
instructions (JSON list), prep_time, cook_time, total_time, servings,
cuisine, difficulty, url, created_at, updated_at, license, image_url,
nutrition, completeness_score`. `completeness_score` (0-100, computed by
`compute_completeness_scores.py`) is a weighted rubric - see
`docs/papers/02-recipe-discovery-engine.md` Section 3 for the exact
weights and a corpus-level finding about what it implies for discovery.
`ingredients`/`instructions` are still stored as JSON-encoded
lists of free-text strings (unchanged - `recipe_ingredients`/
`recipe_steps` are a derived structured index alongside them, not a
replacement for them), which is why the underlying parse is still
heuristic even though its output is now persisted structured data.

`license` values: `MIT` (AkashPS11 + Hieu-Pham batches, ids 1226-15943,
contiguous - AkashPS11 is 1226-2448, Hieu-Pham is 2449-15943), `CC-BY-NC-4.0`
(datahiveai batch, ids 55391-94837 - not contiguous with the MIT range;
there's a gap in between from recipes deleted during earlier development/
testing passes), `user-imported` (anything imported through the app
itself - URL/Paprika/bulk import, quick-add).

## API routes

All under `server.py`'s `RecipeHandler`. HTML page routes render full
pages; `/api/*` routes are JSON.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Home/recent recipes (paginated, `?page=`) |
| GET | `/recipe/<id>` | Recipe view (supports `?servings=N` to scale) |
| GET | `/recipe/<id>/easter-egg` | Comedic riff (generated on demand) |
| GET | `/recipe/<id>/edit` | Edit form, PUTs to the existing `/api/recipe/<id>` on save |
| GET | `/search?q=` | Full-text search, FTS5/`bm25`-ranked (paginated, `?page=`; falls back to quick-add form if empty) |
| GET | `/categories` | All categories with recipe counts |
| GET | `/category/<name>` | Recipes in one category |
| GET | `/import` | Import page (URL / Paprika / bulk JSON) |
| GET | `/add` | Manual recipe entry form (posts to the existing `POST /api/recipe`) |
| GET | `/print?id=` | Print view (`?images=`, `?nutrition=`, `?font=`, `?layout=`) |
| GET | `/history` | Cooking history across the whole collection |
| GET | `/plans` | Meal plan list |
| GET | `/plan/<id>` | Meal plan detail (suggestions + timeline) |
| GET | `/discover?have=` | "What can I make with X" ingredient-coverage discovery |
| GET | `/craving?q=` | Fuzzy-intent search (LLM query planner + flavor index, keyword fallback) |
| GET | `/pairings?ingredient=` | Standalone ingredient lookup (flavor tags, co-occurrence, embedding similarity) |
| GET | `/preferences` | View/edit household dietary restrictions, disliked ingredients, notes |
| GET | `/invent` | Grounded recipe invention form |
| GET | `/lists` | Shopping list index |
| GET | `/list/<id>` | Shopping list detail (checkable items) |
| GET | `/uploads/<filename>` | Serve a locally-uploaded photo (server-generated filenames only) |
| GET | `/pantry` | Pantry page (fresh + needs-confirmation items) |
| GET | `/api/recipes` | JSON recipe list (paginated, `?page=`) |
| GET | `/api/search/fragment?q=` | Live-search JSON endpoint - `{results_html, heading}` HTML snippets, not raw JSON records (same pattern as `/api/plan/<id>/fragment`); powers `/search`'s type-ahead |
| POST | `/api/recipe` | Create a recipe |
| POST | `/api/recipe/import` | Single-URL import |
| POST | `/api/recipe/import/paprika` | Paprika bulk import (`{file_base64}`) |
| POST | `/api/recipe/import/bulk` | Generic bulk import (`{file_base64}`) |
| POST | `/api/recipe/<id>/note` | Add a note |
| POST | `/api/recipe/<id>/cook` | Log a cook-date entry |
| POST | `/api/recipe/<id>/image` | Add a photo (`{url}` or `{file_base64}`) |
| POST | `/api/recipe/<id>/adapt` | Rewrite a recipe's ingredients/instructions to fit saved preferences (LLM) |
| POST | `/api/recipe/invent` | Generate a new recipe from seed ingredients and/or a mood (LLM) |
| POST | `/api/preferences` | Save dietary restrictions/disliked ingredients/notes/hide_builtin_recipes/llm_provider/llm_model/ollama_host - plus llm_api_key, routed to llm_credentials.db instead of recipes.db, blank/absent leaves it unchanged |
| POST | `/api/preferences/test-llm` | Live connectivity check against explicit provider/model/api_key/ollama_host values (not the saved config) - backs the Preferences page's "Test Connection" button, so a config can be validated before saving |
| POST | `/api/plan` | Create a meal plan |
| POST | `/api/plan/<id>/recipe` | Add a recipe to a plan |
| POST | `/api/shoppinglist` | Create a shopping list |
| POST | `/api/shoppinglist/<id>/item` | Add a manual item |
| POST | `/api/shoppinglist/<id>/from-recipe/<recipe_id>` | Add all of a recipe's ingredients, merging matching lines |
| POST | `/api/shoppinglist/<id>/from-plan/<plan_id>` | Add every recipe in a meal plan |
| POST | `/api/pantry` | Add (or refresh) a pantry item |
| PUT | `/api/recipe/<id>` | Update a recipe |
| PUT | `/api/shoppinglist/<id>/item/<item_id>` | Toggle an item's checked state (checking ON also refreshes the pantry) |
| PUT | `/api/pantry/<id>` | Confirm an item is still fresh (resets its shelf-life clock) |
| DELETE | `/api/recipe/<id>` | Delete a recipe |
| DELETE | `/api/recipe/<id>/note/<note_id>` | Delete a note |
| DELETE | `/api/recipe/<id>/image/<image_id>` | Remove a photo (deletes the uploaded file too, if local) |
| DELETE | `/api/recipe/<id>/cook/<entry_id>` | Delete a cook-log entry |
| DELETE | `/api/plan/<id>` | Delete a meal plan |
| DELETE | `/api/plan/<id>/recipe/<recipe_id>` | Remove a recipe from a plan |
| DELETE | `/api/shoppinglist/<id>` | Delete a whole shopping list |
| DELETE | `/api/shoppinglist/<id>/item/<item_id>` | Remove an item |
| DELETE | `/api/pantry/<id>` | Remove a pantry item |

File uploads (Paprika/bulk import) go through base64-encoded JSON bodies,
not multipart/form-data - `http.server` has no built-in multipart parser,
and this keeps every API route in the same simple JSON-body style.

**The routes above are internal**: same-origin implementation details of
this app's own HTML/JS pages, unauthenticated, not a stable contract.

## Public API (`/api/v1/*`)

A separate, versioned, API-key-authenticated, CORS-enabled surface with
full parity with the web app (recipe CRUD, search/discovery, meal
planning + backward scheduling, cooking log, preferences, LLM-grounded
adaptation/invention, import). Routed in `do_GET`/`do_POST`/`do_PUT`/
`do_DELETE` via a `path.startswith('/api/v1/')` check ahead of the
internal-route `if/elif` chain, dispatched to `route_api_v1_get`/`_post`/
`_put`/`_delete`, each of which calls `_require_api_key()` first (except
`GET /api/v1/health`). Every `_api_*` handler is a thin wrapper around the
same `RecipeDatabase`/`MealPlanDatabase`/`query_planner`/
`recipe_adaptation`/`recipe_invention`/`cooking_log`/`categories`/
`preferences` calls the internal routes and HTML pages already use - no
duplicated business logic between the two API surfaces.

Full endpoint reference, auth flow, and request/response examples:
**[docs/API.md](docs/API.md)** - not duplicated here to avoid two
sources of truth drifting apart.

## Known architectural issues

- ~~No connection pooling / concurrency handling~~ **Fixed 2026-07-16**
  (see PROGRESS.md): `run_server()` used a plain `socketserver.TCPServer`
  - single-threaded, one request at a time - so a single slow LLM call
  (any of the 30-120s `urllib` timeouts scattered across
  `query_planner.py`/`recipe_adaptation.py`/`easter_egg.py`/etc., hit for
  real when the LAN Ollama host was unreachable) took the *entire app*
  offline for every concurrent user until it timed out. Now
  `ThreadingRecipeServer` (`ThreadingMixIn` + `TCPServer`,
  `daemon_threads=True`). Each request already opened its own short-lived
  SQLite connection with no shared/global mutable state, so this was
  safe to flip with no other code changes. Paired with switching
  `recipes.db` to WAL mode (`RecipeDatabase.init_database()`) - genuinely
  concurrent requests were structurally impossible before this, so
  write-vs-read lock contention was never a real risk until now.
- **`GET /uploads/<filename>` is a deliberate, narrow exception to "this
  app serves zero static files."** Every other unmatched path 404s
  specifically to avoid the inherited `SimpleHTTPRequestHandler`
  fallback exposing the whole working directory (recipes.db, source,
  `.git`). This one route is safe by construction, not by convention:
  `uploads.resolve_upload_path()` is the only way a filename reaches the
  filesystem, and it rejects anything that isn't a server-generated
  `[a-f0-9]{32}.(jpg|png|gif|webp)` name before ever calling `Path()` on
  it - a client can't get an arbitrary filename into that function in
  the first place, since upload filenames are never derived from client
  input (see `uploads.py`).

## Deployment

Two supported paths, both documented in the README:
- **Native**: `python3 server.py`, zero pip installs for core
  functionality.
- **Docker**: `docker compose up --build`. `docker-compose.yml`
  volume-mounts `recipes.db` from the host rather than baking it into the
  image (the DB is ~450MB as of 2026-07-10, up from ~375MB before the
  FTS5/structured-ingredient/structured-step indexes were added; baking
  it in would make every image rebuild slow and the image itself huge for
  no benefit). `docker-compose.yml` also volume-mounts `uploads/` for the
  same reason - user-uploaded photos need to survive a container rebuild,
  and shouldn't bloat the image. `.dockerignore` excludes
  `.git` (1.6GB in this repo), `.venv`, `data/` (only needed for the
  one-off nutrition backfill), `recipes.db`, and `uploads/`.

The `claude` CLI is intentionally **not** installed in the Docker image -
shipping API credentials in a container image is a bad default. The
easter-egg feature now uses `litellm` against a network-reachable Ollama
instance instead specifically because it works inside a container with no
credential-baking problem; if you configure `SOUS_EASTER_EGG_MODEL` to
point at a paid provider instead, you're responsible for getting that
provider's credentials into the container's environment yourself.
