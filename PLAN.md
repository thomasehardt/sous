# Sous Project Plan

## Phase 1: Project Setup and Dataset Preparation (15-20 minutes)

- [x] Initialize git repository in projects/sous/
- [x] Create basic project structure
- [x] Research and select dataset
- [x] Download dataset (if needed for local testing)
- [x] Set up Python virtual environment

## Phase 2: Core Application Architecture (15-20 minutes)

- [x] Design database schema for recipes
- [x] Implement core recipe data models
- [x] Create basic web server framework
- [x] Implement recipe browsing/search functionality
- [x] Set up file structure for templates and static assets

## Phase 3: Recipe Import and Management (15-20 minutes)

- [x] Implement single-recipe import from URL using schema.org JSON-LD parsing
- [x] Add recipe save/update/delete functionality
- [x] Create recipe view page
- [x] Implement print view for recipes

## Phase 4: Ingredient Pairing Model (15-20 minutes)

- [x] Parse ingredient data from recipes
- [x] Build co-occurrence statistics model
- [x] Create ingredient pairing interface
- [x] Display pairing results

## Phase 5: Docker Packaging and Finalization (15-20 minutes)

- [x] Write Dockerfile
- [x] Write docker-compose.yml
- [x] Test application setup without Docker
- [x] Final documentation review
- [x] Commit all changes

## Phase 6: Testing and Validation (15-20 minutes)

- [x] Validate core functionality
- [x] Test recipe import from URL
- [x] Verify ingredient pairing works
- [x] Confirm application runs correctly
- [x] Review and finalize documentation

## Phase 7: Meal Planning (SPEC.md Phase 2)

- [x] Persist the ingredient-pairing model as a real queryable table
      (previously only ran as throwaway one-off scripts)
- [x] Normalize pairing scores (PMI-style) - raw co-occurrence counts were
      dominated by generic staples (salt, pepper, butter), making every
      recipe surface the same few companion suggestions
- [x] Meal plan + meal plan item data model
- [x] Companion-recipe suggestions (ingredient pairing + cuisine + time/
      difficulty complement)
- [x] Per-recipe backward-scheduling from a target eat time (heuristic
      duration/active-passive extraction from instruction text - documented
      as an estimate, not exact)
- [x] Plan-wide combined timeline with active-step conflict flagging between
      recipes (flagged, not auto-resolved)
- [x] Full CRUD UI/API for meal plans (create, view, add/remove recipes,
      delete)
- [x] Verified live end-to-end: real recipes, real overlapping-conflict
      case, real non-conflict case, real cleanup via delete

## Phase 8: Embedding-Based Pairing (SPEC.md Phase 3)

- [x] get_embedding() / cosine_similarity() (embeddings.py)
- [x] Batch-embed all ingredients with total_count >= 3 (8,568 of 71,882 -
      the long tail below that is mostly raw-import noise)
- [x] get_ingredient_embedding_similarity() / get_embedding_boost()
- [x] Wire the embedding boost into suggest_companions(), on top of (not
      replacing) the existing co-occurrence scoring
- [x] Found and fixed a real perf bug during verification: naive version
      took 9-27s per request; fixed to 0.56-0.99s by scoring cheaply first
      and only running the expensive embedding check against the top 50
- [x] Verified live through the real running server, not just standalone

## Phase 9: Import & Organization (SPEC.md Phase 4)

- [x] Add a real `category` field, separate from `cuisine`; backfill from
      the MIT batch's mislabeled RecipeCategory-as-cuisine data
- [x] Paprika import (.paprikarecipes: zip of gzip-compressed per-recipe
      JSON)
- [x] Generic bulk recipe-JSON import (schema.org-shaped)
- [x] Quick-add flow when a search returns no results

## Phase 10: Personal Cooking Log (SPEC.md Phase 5)

- [x] Recipe notes (free-text, timestamped, per recipe)
- [x] Usage log / calendar (mark a recipe as cooked on a date, view history)

## Phase 11: Recipe Utility (SPEC.md Phase 6)

- [x] Recipe scaling (parse quantities out of free-text ingredients,
      scale by factor or target servings - heuristic, documented limits)
- [x] Print view customization (toggles: images, font size, nutrition,
      layout density)

## Phase 12: Flavor Profiles (SPEC.md Phase 7)

- [x] Define the flavor taxonomy (basic tastes + aromatic/other categories)
- [x] LLM-tag all ~8,568 embedded ingredients against the taxonomy
- [x] Ingredient/dish/meal/cuisine-level flavor queries
- [x] Flavor-profile-level pairing stats (common/rare/never-paired
      combinations), rolled up from existing ingredient-pairing data
- [x] Surface flavor data in suggestions / recipe-creation assistance
- [ ] (Deferred) research an external flavor-pairing dataset to validate/
      enrich the LLM-tagged taxonomy

## Phase 13: Delight (SPEC.md Phase 8)

- [x] Easter-egg funny/mock recipe generation, riffing on a real recipe in
      the user's collection (via the Claude CLI backend)

## Phase 14: Documentation (from 2026-07-14 adversarial UX review)

- [ ] Add format example/placeholder text to the /add ingredient and
      instructions textareas (e.g. "2 cups flour" per line) - currently
      blank, so a new user has to guess the format that scaling depends on
- [ ] Add one-line explanatory copy to "Craving?", "What Can I Make?", and
      "Ingredient Pairings" - what each does and how to phrase a query
- [ ] Explain the meal-plan "Target eat time" field inline (it drives
      backward-scheduling; nothing on the page says so today)
- [ ] Decide whether a short first-run tour/help page is worth building,
      or whether inline copy on each page is sufficient
- [ ] Keep README/ARCHITECTURE/PROGRESS/MISSING_FEATURES current every
      pass (existing practice - restated here since the README test-
      coverage bullet still went stale once more before this review caught it)

## Phase 15: Corpus & Usefulness Strategy (from 2026-07-14 adversarial UX review)

Flagged as the project's likely Achilles heel: verified via direct SQL
that only 36.7% of the 54,722 recipes have both real instructions and at
least one parsed ingredient quantity; 59.4% have neither. No amount of
UI polish fixes a recipe that's just an ingredient name list.

- [x] Break the 36.7%/59.4% headline number down by source batch/cuisine.
      Not diffuse: 32,480 of the 32,481 "both missing" recipes (99.997%)
      fall inside the CC-BY-NC-4.0 batch (ids ~55394-94837, 39,447 recipes
      total) - 82% of that one batch is incomplete. The one outlier,
      recipe 12085 ("Smoked Salmon with Egg Salad and Green beans", MIT
      batch), isn't part of any pattern - a broken import row
      (`ingredients: [""]`, `instructions: []`), worth a one-off cleanup
      separately, not a corpus-wide concern.
- [x] Fix `OLLAMA_HOST` in the deployed container - was unset, resolving
      to the container's own loopback. Fixed 2026-07-14 by recreating the
      container with `OLLAMA_HOST` pointed at the Tailscale IP for
      ollama-interface (the hostname itself doesn't resolve inside the
      container's bridge network, but the IP is reachable). Re-run
      `update_ollama_host` and re-set this if the container is ever
      recreated - the IP isn't guaranteed permanent. Verified live:
      `/craving?q=cozy rainy day soup` now returns "Interpreted as: umami,
      warm spice" instead of falling back to keyword search.
- [x] Decided the product stance: exclude incomplete recipes (no real
      instructions) from search/browse by default rather than just
      rank them lower - they still feed the flavor/pairing tables
      unaffected (that pipeline only ever used ingredient names, never
      quantities, so nothing is lost there), and stay reachable by direct
      link. Backfill/generation was ruled out - inventing instructions
      from a bare ingredient list is content generation, not gap-filling,
      and carries real hallucination risk for a cooking app.
- [x] Ship the completeness signal - turned out **not** to need new
      infrastructure. `completeness_score` (0-100, computed 2026-07-12,
      predates this review) and the `instructions IS NOT NULL` "actually
      cookable" filter already existed and were already wired into
      `find_recipes_by_ingredients()`/`recipe_flavor_index.py` - the one
      path missing it was plain keyword search (`search_recipes()`/
      `count_search_results()` in recipe_model.py), exactly the path that
      surfaced the broken "chicken curry" result. Fixed 2026-07-14: both
      the FTS/bm25 path and the LIKE fallback now filter to
      `instructions IS NOT NULL AND instructions != '[]'` and tiebreak by
      `completeness_score DESC`, matching the existing pattern. Verified
      live: "chicken curry" no longer surfaces the broken recipe;
      `/search` with no query (browse-all) dropped from 54,722 to 21,231
      recipes shown by default. Home page's own "recent recipes" list
      (`get_all_recipes()`) is a separate code path and is unaffected -
      still shows the full 54,722.
- [x] Split `preparation` out as its own `recipe_ingredients` column
      (was folded into the free-text `name` field, e.g. "diced onion"
      instead of name="onion" + preparation="diced") - the
      `ingredient-parser-nlp` library already extracted this as a labeled
      span, just wasn't being kept. Additive: `name`'s existing meaning/
      behavior is unchanged, so no downstream consumer (shopping list,
      flavor tagging, ingredient display) needed updating. Wired into both
      live-write paths (initial backfill, `_sync_structured_ingredients`)
      and `get_structured_ingredients()`. Historical rows backfilled via
      `reparse_ingredients_nlp.py` (idempotent, already used once before
      for the ML-parser swap) - all 517,392 rows, completed in 1541s
      (~26min, ~336 rows/sec); confidence populated for 99.4%, preparation
      for 11.2% (most ingredient lines genuinely have no prep descriptor
      to extract - not a failure rate).
- [x] Found and fixed a real consistency bug surfaced while discussing the
      canonical data model: the recipe detail page's default/print views
      rendered ingredients from raw `recipe.ingredients` text, while the
      *scaled* view rendered from the structured `recipe_ingredients`
      table - same recipe, two different-looking ingredient lists
      depending on whether `?servings=` was set. Both `serve_recipe()` and
      `serve_print_view()` now always render through
      `scale_recipe_to_servings_structured()` (factor=1.0 when not
      scaling), so there's one rendering path, not two. Verified live:
      recipe 2450 shows identical formatted amounts ("2 tsp kosher salt",
      etc.) on both the default and print views; scaling still correct
      (2->8 egg whites at 4x). `_api_get_recipe`'s raw `ingredients` field
      was deliberately left alone - it already exposes `ingredients` (raw)
      and `structured_ingredients` (parsed) as two distinct, named JSON
      fields, so an API consumer can already choose; that's not the same
      silent-inconsistency problem the HTML views had.
- [x] Evaluated RecipeNLG Lite (`m3hrdadfi/recipe_nlg_lite`, MIT, 7,198
      recipes) as a higher-quality 4th source batch: 0% empty
      ingredients/steps, 96%+ have real quantities (vs. ~39% corpus-wide),
      only 6.4% title-overlap with the existing corpus (~6,734 net-new).
      Caveat: ingredients/steps are comma-joined strings in this dataset,
      not proper lists - `import_recipe_nlg_lite.py` written (splits on
      `,`/`. ` and lets the existing `parse_ingredient()` pipeline handle
      the result, deliberately not inventing new merge heuristics) but
      **not yet run**.
- [x] Evaluated `irkaal/foodcom-recipes-and-reviews` (Kaggle, CC0, 522,517
      recipes) - **ruled out**. 78.0% of all rows have mismatched
      `RecipeIngredientQuantities`/`RecipeIngredientParts` array lengths
      (verified on the full dataset via kagglehub - your `~/.kaggle/
      access_token` already worked for this, no setup needed), often
      missing 2-5+ ingredient names per recipe with no fallback free-text
      field to recover them from. Looks structured until you check it;
      not usable as-is.
- [x] **Got the real RecipeNLG** (`recipenlg.cs.put.poznan.pl`, 2,231,142
      recipes) - required a manual click-through download the first
      pass through this list incorrectly treated as a reason to skip
      rather than ask about (corrected going forward). User downloaded it by hand to
      `~/Downloads/dataset.zip`; extracted to `/tmp/.../scratchpad/
      recipenlg_full/dataset/full_dataset.csv` (2.29GB). Verified on the
      full file: 0% empty ingredients/directions, only 0.51% with zero
      quantities anywhere, clean JSON-array format (not misalignment-prone
      like the Kaggle dataset), 13.47% title-overlap with the existing
      corpus, 42.89% duplicate titles *within* RecipeNLG itself (real
      dedup needed on import, not just against the existing corpus).
      Same non-commercial license tier as the CC-BY-NC-4.0 batch already
      in the corpus.

  **PAUSED HERE - resume point.** Two things pending, in order:
  1. **Open decision, not yet made**: user floated using RecipeNLG as
     the *only* source going forward (full replace, not additive). I
     pushed back with real numbers before pausing: the existing corpus
     has 39,563 recipes (72%) with real images and 40,668 (74%) with
     real nutrition - RecipeNLG has neither field, ever, so full
     replacement means permanently losing that, not just superseding it.
     No cook-log/notes/meal-plan data exists yet in this instance
     though, so there's no orphaned-user-content risk either way. My
     recommendation was additive (keep existing corpus, import RecipeNLG
     alongside it, let the completeness-based search ranking already
     shipped this session naturally surface the better data) - but this
     is the user's call, not decided yet.
  2. **Once the add-vs-replace question is settled**: write and run the
     real RecipeNLG import script. Two known engineering costs to solve
     before running it at 2.2M-recipe scale, not yet solved:
     - **Dedup**: against the existing corpus by title (same pattern as
       `import_recipe_nlg_lite.py`) *and* against RecipeNLG's own
       42.89% internal duplicate-title rate - both needed, only the
       first is built today.
     - **Parse-time cost at scale**: `save_recipe()` parses every
       ingredient line synchronously via the ML model - at the ~44-170
       rows/sec rates observed elsewhere this session, parsing every
       ingredient line of 2.2M recipes (even after dedup, likely
       1M+ remaining) could take many hours to days. Was mid-check on
       whether `ingredient_parser.parse_multiple_ingredients()` (found
       via `dir(ingredient_parser)` - not yet inspected) offers batched/
       faster inference before this session paused - check that first,
       since it may change the import script's design (batch-parse then
       bulk-insert, vs. today's `save_recipe()`-per-recipe pattern).
  Full RecipeNLG dataset is NOT copied into the repo (2.29GB, stays in
  `/tmp` scratch) - if the scratch dir gets cleared before the import
  runs, the user will need to re-point at `~/Downloads/dataset.zip`
  (still on disk as of this pause) and re-extract.
- [ ] Investigate LLM-based backfill of missing instructions/quantities -
      deprioritized given the exclude-by-default decision above; revisit
      only as an explicit, clearly-labeled "AI-assisted completion"
      feature a user opts into per-recipe, never a silent corpus edit
- [ ] Re-measure the 36.7%/59.4% numbers periodically as new recipes get
      added (manual entry, import) to confirm the gap doesn't reappear
      now that it's not enforced at write time, only at read time

## Phase 16: Polish (from 2026-07-14 adversarial UX review)

- [x] Fix the empty-query search header ("Search Results for \"\"" reads
      oddly over the full recipe list - something like "Browse all
      recipes" instead). `/search` with no `q` now heads "Browse all
      recipes" (page `<title>` follows suit: "Browse Recipes - Sous").
- [x] Add servings and cuisine fields to the manual /add form - the
      backend (`handle_create_recipe`) already accepted both, this was
      purely a missing-fields gap in `get_add_recipe_form_html()`. Also
      added placeholder example text to the ingredients/instructions
      textareas while touching this form (overlaps Phase 14's first
      item). Verified live: submitted a real recipe with servings=12,
      cuisine=american through the actual form, confirmed both persisted
      via direct SQL, then deleted it through the real `/api/recipe/<id>`
      DELETE endpoint and confirmed the cascade left no stray rows.
- [x] Distinguish view vs. edit mode on the recipe page - photo upload/
      remove controls were visible by default even when just browsing.
      Now hidden behind a "Manage photos" toggle (`.gallery.editing`
      class flip); `refreshGallery()` untouched since it only reaches
      into `#gallery-thumbs` and `.recipe-hero-image`, both unaffected by
      the new wrapper. Verified live: default view hides the controls,
      clicking "Manage photos" reveals them and flips the label to "Done".

All three verified against the real running container (rebuilt image,
recreated `sous-local`) via headless-Chrome CDP, not just read from code.
Full 91-test suite re-run clean after the change (`.venv/bin/python -m
pytest tests/ -q`). See PROGRESS.md.

## Phase 17: Pluggable LLM Provider (Ollama / Claude / Gemini / whatever)

Every LLM call in the app is hand-wired to a local/LAN Ollama instance today
- fine while there was one provider, but it means today's `OLLAMA_HOST`
outage (Phase 15) took down every LLM-backed feature at once, and there's
no way to point Sous at a hosted model instead. Seven call sites, found via
`grep -rln "OLLAMA_HOST\|litellm" *.py`:

- `embeddings.py` - raw `urllib.request` to `{OLLAMA_HOST}/api/embeddings`
  (ingredient-pairing embeddings)
- `flavor_tagging.py`, `query_planner.py`, `pantry_shelf_life.py`,
  `recipe_adaptation.py`, `recipe_invention.py` - raw `urllib.request` to
  `{OLLAMA_HOST}/api/chat`, each with its own JSON-mode prompt-and-hope
  parsing (`format: "json"` + manually `json.loads()`-ing the reply)
- `easter_egg.py` - the one outlier, already goes through `litellm`
  (`api_base` pointed at Ollama) rather than raw HTTP

- [x] Design one small provider-agnostic interface (`llm_client.py`)
      exposing `chat()`/`chat_json()`/`embed()`, and migrate all 7 sites
      onto it - no more per-file `OLLAMA_HOST` reads or hand-rolled
      `urllib.request` calls. Done 2026-07-16, see PROGRESS.md.
- [x] Decide the abstraction: `litellm` for all 7 sites (chosen over a
      hand-written adapter - least new code, already a dependency).
- [x] Provider selection - went further than the original env-var-only
      plan: **UI-configurable** on `/preferences` (saved to the DB,
      takes effect immediately, no restart), falling back to
      `SOUS_LLM_PROVIDER`/`SOUS_LLM_MODEL` env vars, falling back to the
      original Ollama/`qwen3:8b` default. The env-var-only version
      wouldn't have actually fixed the problem this phase exists to
      solve - switching still would've meant editing container env vars
      and restarting, the exact friction a live outage doesn't have time
      for.
- [x] API keys, also UI-configurable (added same day, after initial
      user feedback that "select" wasn't the same as "configure") -
      pasted directly into a field on `/preferences`, stored in a new
      `llm_credentials.db` (a *separate* gitignored SQLite file, never
      `recipes.db`), falling back to the provider's standard env var
      (`ANTHROPIC_API_KEY`/`GEMINI_API_KEY`) if nothing's saved. No
      `.env` file management required for a first-time setup with a
      hosted provider. Verified with a real (fake) key: the request
      actually reached Anthropic's API and got a genuine
      `authentication_error`, not a "key missing" error - proof the key
      is really transmitted, not just accepted and dropped.
- [x] Ollama's connection detail (host/port - it has no API key, but
      does need a reachable address), also UI-configurable via a new
      `ollama_host` preference field, same three-tier priority as
      provider/model. Verified live: pointed it at a deliberately
      unreachable IP, confirmed the request actually targeted the new
      address (a real 90s connection timeout, not the old default's
      fast-refuse), then reverted.
- [ ] Claude structured outputs (`client.messages.parse()`) - NOT done.
      Kept the uniform `response_format={"type": "json_object"}` +
      prompt-for-JSON pattern across all providers via litellm rather
      than a native per-provider code path, for one shared code path
      instead of Claude getting special-cased logic - real potential
      quality/reliability improvement if Anthropic becomes the daily
      driver, left for whoever picks this up next.
- [x] Gemini: `litellm`'s `"gemini/<model>"` prefix, no separate code
      needed - satisfied automatically by the litellm choice above.

## Phase 18: Backlog from live user feedback (2026-07-16, not yet started)

Three items flagged in passing during the same session as Phase 17,
deliberately deferred rather than worked mid-conversation:

- [ ] **Normalize leading bullet markers in instruction steps.** Scoped:
      75 recipes / 162 steps have a step starting with `\-`, `- `, `* `,
      or `•` baked into the text (source-scrape noise) - redundant since
      the recipe page already numbers steps via `<ol>`. Strip the
      leading marker at render time (or backfill `recipes.instructions`
      directly, matching this project's usual backfill-script pattern -
      see `backfill_paprika_photos.py` etc. for the convention). Example:
      recipe 95161 ("Instant Pot Pho"), every one of its 6 steps starts
      with `\-`.
- [ ] **"Comedic riff" should stay grounded in the real recipe.**
      Currently `easter_egg.py` generates a fully invented riff (see its
      own docstring: "Deliberately ungrounded... contrast with
      recipe_invention.py"). User wants the *actual* recipe title/
      ingredients/instructions preserved and humor injected into/around
      them, not a freeform comedic invention - closer in spirit to
      `recipe_adaptation.py`'s "smallest changes to the original" model
      than `recipe_invention.py`'s from-scratch generation. Needs a new
      prompt (rewrite `_build_prompt()` to ground it in the real recipe
      and ask for the original content back with humor woven in, not a
      riff) and probably a different rendering approach (currently
      `serve_easter_egg()` just dumps the LLM's raw text in a `<pre>` -
      grounded output would want real ingredient/instruction structure,
      more like the recipe page itself).
- [ ] **Compact "recipe card" print format.** The existing `/print`
      view (`serve_print_view()`) already has `?layout=compact`/
      `?font=`/`?images=`/`?nutrition=` options, but "very short" recipe
      card format (index-card-sized, minimal chrome) is a different
      target than what `compact` currently does (just tighter margins/
      font on the same full layout) - likely a new `layout=card` option
      alongside the existing ones, not a replacement.
- [x] ARCHITECTURE.md/README.md updated.