# Sous

A self-hosted recipe manager. Browse, search, and organize a 54,000+ recipe
collection with categories, meal planning with backward-scheduled cooking
timelines and generated shopping lists, ingredient-pairing and flavor-profile
suggestions, fuzzy-intent ("something comforting for cold weather") recipe
discovery, preference-aware recipe adaptation and grounded invention, recipe
scaling, a personal cooking log, and a versioned public API. No accounts, no
cloud, no external services beyond what you explicitly import from or point
the optional LLM features at.

For how it's built (module map, database schema, design decisions), see
[ARCHITECTURE.md](ARCHITECTURE.md). For the full feature history and every
verification that went into building it, see [PROGRESS.md](PROGRESS.md).
For what's still missing or incomplete, see
[MISSING_FEATURES.md](MISSING_FEATURES.md). For the versioned, API-key
authenticated public API, see [docs/API.md](docs/API.md). For measured,
cited, scientific-paper-style write-ups of the bespoke engines behind
flavor pairing, recipe discovery, meal scheduling, and generation, see
[docs/papers/](docs/papers/).

## Quickstart

### Native (no Docker)

Requires only the Python standard library - no `pip install` needed for
core functionality.

```bash
python3 server.py
```

Then open `http://localhost:8000`. If port 8000 is already in use on your
machine, override it with the `PORT` env var:

```bash
PORT=8080 python3 server.py
```

### Docker

```bash
touch llm_credentials.db  # required once - see why below
docker compose up --build
```

Then open `http://localhost:8000`. `docker-compose.yml` mounts your local
`recipes.db` into the container, so data persists on the host and survives
container rebuilds.

The first run needs a `recipes.db` to already exist (even an empty one is
fine - `python3 server.py` or `python3 -c "from recipe_model import
RecipeDatabase; RecipeDatabase()"` will create one with an empty `recipes`
table and the right schema).

The `touch llm_credentials.db` step is required, not optional: this file
(gitignored, holds any LLM provider API keys you enter on `/preferences`)
doesn't exist in a fresh clone, and Docker silently bind-mounts a
*directory* instead of a file at that path if it's missing when the
container first starts - which breaks the Preferences page. `recipes.db`
doesn't have this problem because you already have to create it first;
`uploads/` doesn't have it because it's meant to be a directory.

### Running tests

```bash
pip install pytest  # not a core dependency - see requirements.txt
pytest tests/
```

Every test gets its own throwaway SQLite file (pytest's `tmp_path`), created
through the same `RecipeDatabase`/`MealPlanDatabase`/module `init_*_table()`
calls the app itself uses - never the real `recipes.db`. See
[Known limitations](#known-limitations) for what this suite does and
doesn't cover.

## Feature tour

### Browse, search, import
- Home page: search box first, then a compact "today's pick" (one recipe
  with a photo, deterministically the same one all day - not a fresh
  random pick on every reload), then recent recipes. Full-text search
  (SQLite FTS5, ranked by relevance) across title/description/
  ingredients, paginated, results updating live as you type (debounced),
  no need to press Enter. `Categories` is a top-level nav link.
- Single-recipe import from any URL that publishes schema.org `Recipe`
  JSON-LD (most modern recipe sites do) - on the `/import` page.
- Bulk import on the same page: upload a Paprika `.paprikarecipes` export,
  or a generic JSON file (one schema.org-shaped recipe object, or a list
  of them). Paprika imports also carry over each recipe's photo -
  preferring the embedded photo Paprika stores locally over the original
  web image link when both are present.
- **`/add`** - type in a recipe by hand (title, ingredients, instructions)
  when you don't have a URL or export file. If a search comes up empty,
  the same form is offered right there too, prefilled with your query.
- **Edit** - "Edit Recipe" on any recipe page (`/recipe/<id>/edit`) opens
  a form pre-filled with its title, description, ingredients,
  instructions, times, servings, cuisine, and difficulty. Photos have
  their own editing UI on the recipe page itself, not this form.
- **Categories**: browse `/categories` for an index of every category
  with recipe counts, or click a category tag on any recipe.

### Recipe discovery
Three distinct ways to find something to cook, each suited to a
different question:
- **`/discover?have=`** - "what can I make with what I have": list
  ingredients you're holding, get recipes ranked by how many they use,
  with what's missing called out. A structured query, no model involved.
- **`/craving?q=`** (marked with an `LLM` badge, in the nav and on the
  page) - free-text mood/intent search ("something comforting for cold
  weather," "use up leftover rice"). Your query is sent to the active
  LLM provider (see Preferences), which translates it into flavor
  categories, cuisine, and a time constraint against a fixed vocabulary;
  results are ranked against a precomputed per-recipe flavor index.
  Shows an "Interpreted as: ..." line so you can see how your query was
  read, and falls back to plain keyword search if nothing is extracted
  or nothing matches.
- **`/pairings?ingredient=`** - standalone lookup for one ingredient:
  its flavor tags, what it most often co-occurs with, and what's
  semantically similar by embedding (catches near-synonyms like "lime"
  for "lemon" that never literally appear in the same recipe).
- Measured latency, ranking methodology, and a corpus limitation that
  bounds two of the three paths: [Technical Report No. 2](docs/papers/02-recipe-discovery-engine.md).

### Meal planning
- Create a meal plan (name + target eat time), add multiple recipes to it.
- **Companion suggestions**: given one recipe, get others that pair well,
  scored by ingredient co-occurrence (PMI-normalized, so it doesn't just
  surface "goes with salt" for everything) plus embedding-based semantic
  similarity, cuisine match, and time/difficulty complement. Each
  suggestion shows its dominant flavor tags alongside the seed recipe's.
- **Backward scheduling**: given a target eat time, get a per-recipe and
  whole-plan cooking timeline worked backward from when you want to eat,
  with active-step conflicts between recipes flagged. Step durations and
  active/passive classification are heuristically parsed from free-text
  instructions (there's no structured timing data in the source
  datasets) but persisted as structured data, not re-parsed on every
  request - still a genuinely useful estimate, not a guarantee.
- Companion-suggestion methodology and measured scheduling accuracy:
  [Technical Report No. 1](docs/papers/01-flavor-pairing-engine.md) and
  [Technical Report No. 3](docs/papers/03-time-planning-engine.md).

### Shopping lists
- `/lists` - create as many named lists as you want. Generate one from a
  single recipe ("Add all ingredients to a shopping list" on the recipe
  page) or from a whole meal plan ("Generate shopping list from this
  plan"), or add items manually.
- **Quantities merge, not duplicate**: adding two recipes that both call
  for "2 cups flour" produces one "4 cups flour" line, not two - matched
  on the same canonical ingredient name and unit (the same normalization
  the rest of the app uses). Ingredients with different units for the
  same name (e.g. "2 cups flour" and "3 tbsp flour") stay as separate
  lines rather than being cross-unit converted - there's no reliable
  unit-conversion table in this project, and a silently wrong conversion
  would be worse than two honest lines.
- Check items off while you shop; state persists. Remove items you don't
  need.
- Checking an item off is treated as "I just bought this" and
  automatically restocks it in your [pantry](#pantry) - no extra effort
  required to keep the two in sync.

### Pantry
- `/pantry` - what you have on hand, retained across visits (unlike
  `/discover`'s have= list, which you type in fresh every time). Add
  items manually, or let checking things off a shopping list restock
  them for you.
- **Shelf-life-aware, not a flat inventory list.** Every ingredient is
  classified into one of five shelf-life categories (highly perishable
  through shelf-stable) via the same local-LLM-tagging approach used for
  flavor profiles. An item well past its typical shelf life is
  discarded automatically - old knowledge doesn't linger as if it were
  still true. An item *approaching or just past* its shelf life is
  flagged "needs confirmation" instead of either being silently trusted
  or silently dropped - the app asks, it doesn't assume.
- "Find recipes using my pantry" on the pantry page feeds your
  currently-confirmed-fresh items straight into `/discover` - items
  awaiting confirmation are deliberately excluded from that list until
  you say yes or no.

### Flavor profiles
- Every ingredient with enough data has been tagged against a 17-category
  flavor taxonomy (5 basic tastes + 12 aromatic categories) via LLM
  classification.
- Query the flavor profile of an ingredient, a recipe, a cuisine, or a
  whole meal plan.
- See which flavor combinations are common, rare, or never observed
  across the whole collection, rolled up from real ingredient pairing
  data.

### Recipe utility
- **Photos**: any number of photos per recipe, not just one. Add by URL
  or by uploading a file directly (JPEG/PNG/GIF/WebP, 10MB max) - real
  content-sniffing validates what you upload actually is an image
  regardless of filename, and uploaded files get a random server-
  generated name (never anything derived from what you uploaded), so
  there's no path-traversal surface. The first photo doubles as the
  thumbnail shown everywhere else in the app (search results, home page,
  etc.) automatically. A recipe with no photo shows a prompt inviting you to add
  one, instead of just leaving that space blank.
- **Scaling**: on any recipe page, enter a target serving count to scale
  every ingredient quantity. Parses fractions, unicode fraction glyphs,
  decimals, and mixed numbers from free-text ingredients - only the
  leading quantity is scaled (e.g. `"2 (8 oz.) cans"` scales the `2`, not
  the `8`), and results are meant to be sanity-checked for unusual amounts.
- **Print view**: a print-friendly layout with toggles for images,
  nutrition info, font size, and layout density. Real images and nutrition
  facts are available for about 75% of the collection (see
  [Known limitations](#known-limitations)).

### Personal cooking log
- Add free-text, timestamped notes to any recipe.
- Mark a recipe as cooked; see your cooking history both per-recipe and
  across the whole collection at `/history`.

### Preferences
- `/preferences` - one household-level record (no multi-user accounts
  exist, so no per-user records either): dietary restrictions, disliked
  ingredients, and free-text notes for anything else ("keep sodium low,"
  "we don't eat pork"). Recipe pages show a "Heads up: contains X" note
  when a disliked ingredient is present, with suggested substitutes.
- This is the grounding data for adaptation and invention below - a
  disliked ingredient is excluded from the LLM's options at retrieval
  time, not just requested to be avoided after the fact.
- "Hide built-in recipes" toggle: restricts every browsing surface (home,
  search, categories, discover, craving) to just the recipes you've added
  yourself (`license='user-imported'`), hiding the bulk-imported corpus.
  Persists across sessions like the rest of this page.
- **LLM Provider**: pick which model powers every LLM-backed feature
  (flavor tagging, pantry shelf-life, craving search, recipe adaptation/
  invention, the comedic riff) - Ollama (the default, no API key needed),
  Anthropic, or Google/Gemini, plus a model name. Everything needed to
  actually use it is configurable right here, not just selectable - no
  container restart or `.env` file required for any of it:
  - **Ollama**: a "host" field to point at your LAN box (or localhost,
    or wherever) - swap boxes without touching container env vars.
  - **Anthropic/Gemini**: paste the API key directly into the field that
    appears, saved locally (a separate file,
    [`llm_credentials.db`](#docker), never the recipe database or git -
    see below). The field stays blank on reload and just shows "already
    set" - it never re-displays a saved key.
  - Env vars (`OLLAMA_HOST`/`ANTHROPIC_API_KEY`/`GEMINI_API_KEY`/
    `SOUS_LLM_PROVIDER`/`SOUS_LLM_MODEL`) still work as a fallback for
    anything not set in the UI, if you'd rather manage it that way.

### Recipe adaptation & invention
- **Adapt an existing recipe**: on any recipe page, "Adapt This Recipe"
  rewrites its ingredients/instructions to fit your saved preferences via
  a local LLM, making the smallest changes that satisfy them - a
  substitution and a note change, not a rewrite from scratch. Review the
  draft, then save it as a new recipe if you like it.
- **Invent a new recipe**: `/invent` (marked with an `LLM` badge, in the
  nav and on the page) takes ingredients you have and/or a mood, expands
  them into a candidate ingredient palette using real co-occurrence
  statistics from the whole collection (not free association), and asks
  the active LLM provider to write a new recipe grounded in that
  palette. Contrast with the "Comedic riff" below, which is
  intentionally ungrounded - invention aims to be genuinely cookable.
- Both are documented in depth, with real measured examples, in
  [Technical Report No. 4](docs/papers/04-recipe-adaptation-invention-engine.md).

### Public API
- A versioned, API-key-authenticated JSON API at `/api/v1/*` with full
  parity with the web app - recipe CRUD, search/discovery, meal planning
  and backward scheduling, cooking log, preferences, and the LLM-grounded
  adaptation/invention features. Separate from the plain `/api/*`
  endpoints the web UI's own pages use internally (those stay
  unauthenticated, same-origin only, and aren't a stable contract).
- Keys are created via a CLI (`python3 manage_api_keys.py create "label"`)
  since there's no in-app account system to gate a "create key" endpoint
  behind; stored as SHA-256 hashes, never in plaintext. CORS is wide open
  so it can be called from other tools/devices.
- Full endpoint reference, auth details, and examples: **[docs/API.md](docs/API.md)**.

### Delight
- Every recipe page has a "Comedic riff" button that generates an
  on-demand, unpersisted, genuinely funny mock recipe riffing on the real
  one - powered by the same LLM Provider setting as everything else (see
  Preferences above). If generation fails for any reason, the button
  degrades gracefully to an honest "couldn't generate one right now"
  message instead of erroring out.

## Known limitations

These are documented gaps, not bugs - each was investigated and the
limitation is in the underlying data or an inherent scope tradeoff, not an
oversight.

- **~13,495 recipes (the Hieu-Pham source batch, ids 2449-15943) have no
  image or nutrition data.** That source dataset simply doesn't include
  it (no nutrition columns at all, and its image field is a bare filename
  slug with no host to resolve against) - nothing was silently dropped.
- **Recipe scaling and cooking-step timing extraction are both
  heuristic** (regex/keyword parsing over free-text - there's no
  ground-truth structured data in the source datasets to parse from
  instead), though both are now computed once and persisted as real
  structured data (`recipe_ingredients`: quantity/unit/name per
  ingredient line; `recipe_steps`: duration/type per instruction step)
  rather than re-derived on every request. Both are useful
  approximations, not guarantees - sanity-check unusual results.
- **39,447 recipes are licensed CC-BY-NC-4.0** (non-commercial use only).
  Every recipe's `license` field tracks this, so they can be filtered out
  if this app is ever redistributed or sold - fine for personal
  self-hosted use as-is.
- **Automated test coverage is targeted, not exhaustive.** `tests/` (91
  tests) covers the highest-risk pieces of business logic: shopping-list
  quantity merging, pantry shelf-life decay/confirmation thresholds,
  backward-scheduling conflict detection, ingredient quantity parsing/
  scaling, and the flavor-aggregation/discovery queries - see
  [Running tests](#running-tests) below. Most of the rest of the app is
  still verified by hand against a live running server and real SQL
  queries (see PROGRESS.md for the specifics of each), which remains
  true and deliberate for anything involving the LLM or the live corpus,
  where a live check is more meaningful than a mock.

## Out of scope

User accounts, multi-user support, cloud sync, a mobile app, and
live-cooking-assistance API integrations (i.e. Sous *consuming* a
third-party cooking-assistant API - unrelated to Sous's own public API,
which is built) are explicitly scoped out - see SPEC.md for the
reasoning behind each. In-app shopping lists were also deferred at one
point but are no longer out of scope - see the feature tour above.
