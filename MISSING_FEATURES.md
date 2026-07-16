# Missing / incomplete features

Sous's PLAN.md is fully checked off (every phase in SPEC.md has been
built and verified), but "checked off" isn't the same as "nothing's
missing." This doc is the single place that answers "what's actually
still gappy" - previously this was scattered across README's "Known
limitations", one deferred PLAN.md checkbox, and PROGRESS.md's
"What's Next" notes. Re-verified against current code on 2026-07-14,
not just copied from those older notes - see the note on each item.

## Resolved (2026-07-13)

- **Manual recipe entry was undiscoverable.** The only way to reach the
  quick-add form (title + ingredients + instructions -> `POST
  /api/recipe`) was to search for a title that didn't exist yet and use
  the empty-results state - there was no direct link to it anywhere.
  Fixed with a standalone `/add` page (shares the same form/backend via
  a new `get_add_recipe_form_html()` helper) and a nav entry. See
  PROGRESS.md.
- **Nav bar had grown to 14 flat, ungrouped links**, wrapping to two
  lines with no hierarchy as Pantry/Shopping Lists/Add Recipe/
  Preferences/History landed over the session without a grouping pass.
  Regrouped into four `<details>/<summary>` dropdowns (Add, Discover,
  Plan, You); Home/Search stayed flat. See PROGRESS.md, ARCHITECTURE.md.
- **Photo-delete triggered a full-page reload**, a regression against
  the "no gratuitous reload" principle established 2026-07-11 -
  introduced when multi-photo support shipped and never caught until
  the next rubric re-score. `refreshGallery()` now also syncs the hero
  image in place instead of falling back to `location.reload()`. See
  PROGRESS.md.

## Resolved (2026-07-10)

The items formerly listed here under "Needs attention" and "Documented
tradeoffs" are fixed, verified live, and committed:

- **Unescaped HTML / stored-XSS.** Every recipe/user-derived interpolation
  site in `server.py` (title, description, ingredients, instructions,
  notes, category names, plan names, search query, etc.) now goes through
  `escape_html()`. Verified by round-tripping a recipe with
  `<script>`/`<img onerror>` payloads in title/description/ingredients/
  instructions and confirming they render as literal text, plus the
  reflected `?q=` search-query XSS.
- **`delete_recipe()` cascade.** Now deletes matching rows from
  `recipe_categories`, `recipe_notes`, `cook_log`, `recipe_steps`,
  `meal_plan_items`, and `recipes_fts` in the same transaction (tables
  that don't exist yet are skipped, not an error). Verified against a
  scratch DB with one row seeded in each table.
- **Pagination.** `get_all_recipes()`/`search_recipes()` take `limit`/
  `offset`; home and search both page at 24/page with Prev/Next controls
  and a total count, wired via `?page=`.
- **Full-text search.** Replaced `LIKE '%query%'` with a SQLite FTS5
  index (`recipes_fts`, porter+unicode61 tokenizer) over title/
  description/ingredients, ranked by `bm25()`. User input is tokenized
  and turned into a safe prefix-AND query (`_build_fts_match()`) rather
  than passed to `MATCH` raw, with a LIKE fallback if that ever fails.
  Self-initializing: `init_database()` backfills the index on first run
  against any existing `recipes.db`, so this didn't require a data
  migration step. Verified: multi-word queries rank properly (`chicken
  curry` surfaces "Malaysian Chicken Curry" first), prefix matching works
  (`chick` finds "Chicken"/"Chickpea"), and query-syntax characters
  (`" * ( ) -` etc.) no longer risk a MATCH syntax error.
- **Structured scaling/cook-time data model.** New `recipe_ingredients`
  table (`quantity`/`unit`/`name` parsed from each ingredient line via
  `recipe_scaling.parse_ingredient()`, a new unit-alias table covering
  ~25 common units) and an eager full backfill of `recipe_steps`
  (previously only populated lazily, one recipe at a time, on first
  schedule request - now covers all 15,269 recipes with instructions
  up front). Both self-initializing like the FTS index: `init_database()`
  backfills on first run against any existing `recipes.db`. The
  extraction itself is still heuristic (no ground-truth structured
  ingredient/timing data exists in the source datasets to parse from
  instead - see README's original tradeoff note) - what changed is it's
  computed once and persisted/queryable, not re-derived live on every
  scale or schedule request. The recipe-page scaling form now reads
  `get_structured_ingredients()` instead of re-parsing raw text.
  Verified: backfilled 495,503 ingredient rows and 73,205 step rows
  against a scratch copy of the real `recipes.db` (~5s total); a caught-
  and-fixed bug where the backfill's "is the table empty" check wrongly
  treated 26 pre-existing lazily-cached `recipe_steps` rows (from a much
  earlier session) as "already fully backfilled" - fixed to check
  per-recipe coverage instead; scaling verified live end-to-end through
  the real server (recipe 2450: "2 teaspoons kosher salt" x4 servings ->
  "8 tsp kosher salt", correct); cascade-delete verified to also clean up
  `recipe_ingredients`.

## Resolved (2026-07-14)

- **Three Phase 16 polish items from the UX review**: the empty-query
  search header ("Search Results for \"\"" -> "Browse all recipes"),
  the manual `/add` form missing servings/cuisine fields, and the
  recipe page always showing photo-edit controls even when just
  browsing (now behind a "Manage photos" toggle). See PLAN.md Phase 16,
  PROGRESS.md.
- **LLM features were dark in the running `sous-local` container.**
  `OLLAMA_HOST` wasn't set in the container's environment, so
  `flavor_tagging.py`/`pantry_shelf_life.py`/`embeddings.py`/the
  craving-query planner all defaulted to `http://localhost:11434`
  (the container's own loopback) and could never reach the host's real
  Ollama. Found live during the adversarial UX review:
  `/craving?q=cozy rainy day soup` silently fell back to plain keyword
  search and returned zero results. Fixed same-day by recreating the
  container with `OLLAMA_HOST` pointed at the Tailscale IP for
  ollama-interface (looked up via the `update_ollama_host` shell
  function - the hostname itself doesn't resolve inside the
  container's bridge network, but the IP is reachable from it).
  Verified live: the same query now returns "Interpreted as: umami,
  warm spice" instead of falling back. Note for next container
  rebuild: re-run `update_ollama_host` and re-pass the current IP, since
  it isn't guaranteed permanent. See PLAN.md Phase 15, PROGRESS.md.

## Resolved (2026-07-16)

- **Every LLM-backed feature was hardwired to Ollama, with no way to
  switch without editing container env vars and restarting.** Made
  concrete the same day: the LAN Ollama host went unreachable mid-session
  and, combined with the server being single-threaded at the time (also
  fixed same day - see PROGRESS.md), took the *entire app* offline, not
  just the LLM features, until whichever request was stuck timed out.
  Fixed via PLAN.md Phase 17: `llm_client.py` (litellm-backed,
  provider-agnostic) migrated all 7 LLM call sites off hand-rolled
  `urllib.request`/`OLLAMA_HOST` reads, with provider/model now
  UI-configurable on `/preferences` (takes effect immediately, no
  restart - a bigger fix than Phase 17's original env-var-only plan,
  which wouldn't have actually solved the "outage with no time to
  restart a container" problem). Verified live end-to-end: selecting
  Anthropic with no `ANTHROPIC_API_KEY` configured produces a clear,
  fast (<10ms) "needs ANTHROPIC_API_KEY set" error from the real feature
  code, not a hang or a generic failure.

## Documented tradeoffs (still true, re-confirmed)

- **Most of the corpus is instructions/quantities-incomplete, not just
  image-incomplete.** Verified via direct SQL 2026-07-14: only 36.7%
  of the 54,722 recipes have both real instructions *and* at least one
  parsed ingredient quantity; 59.4% have neither (source-dataset rows
  that were only ever an ingredient name list to begin with, not a
  Sous parsing bug). Search/discovery surface these with no
  completeness signal - e.g. the top hit for "chicken curry" has no
  instructions and no ingredient quantities at all. The pre-existing
  bullet below only tracked the narrower *image*-coverage gap.
- ~13,495 recipes (the Hieu-Pham source batch, ids 2449-15943) have no
  image or nutrition data - the source dataset never had it. **Confirmed
  fine as-is by the user 2026-07-10** - not every recipe needs an image.
  Also produced a real feature idea: support *multiple* images per
  recipe, not just a single `image_url`. **DONE, 2026-07-13** - see
  `recipe_images.py`, PROGRESS.md. (Per-step image association, as
  opposed to a plain per-recipe gallery, remains unbuilt.)
- 39,447 recipes are CC-BY-NC-4.0 licensed (tracked per-recipe, filterable
  if this is ever redistributed, fine for personal use). User flagged
  2026-07-10 to keep this in mind going forward - no action needed now.
- No automated test suite - every feature has been verified by hand
  against a live server + direct SQL, per PROGRESS.md. **Flagged by the
  user 2026-07-10 as something to actually build.** **Built, 2026-07-13,
  expanded 2026-07-13** - `tests/` (pytest, 91 tests), scoped to the
  highest-risk/highest-complexity pieces of business logic (shopping-list
  merging, pantry decay, scheduling conflicts, ingredient quantity
  parsing/scaling, flavor-aggregation/discovery queries), not the whole
  app - see ARCHITECTURE.md's "Testing" section and PROGRESS.md. Most
  other features remain hand-verified
  against a live server, deliberately, since that's a more meaningful
  check than a mock for anything touching the LLM or the live corpus.

## Deferred (in PLAN.md, intentionally not done)

- Validate/enrich the LLM-tagged flavor taxonomy against an external
  flavor-pairing dataset - Phase 12's last unchecked item, no immediate
  need identified.
- Three items from live user feedback, 2026-07-16 (PLAN.md Phase 18,
  not started): normalizing leading bullet-marker noise in instruction
  steps (75 recipes/162 steps scoped), grounding the "Comedic riff" in
  the real recipe instead of a fully invented riff, and a compact
  "recipe card" print layout distinct from the existing `?layout=compact`.

## Explicitly out of scope (see SPEC.md for the reasoning)

User accounts / multi-user support, cloud sync, a mobile app, and
live-cooking-assistance API integrations. (Grocery-list integration was
listed here too until 2026-07-13 - it's now built, as in-app shopping
lists rather than third-party integration; see PROGRESS.md.)
