# Sous Project Progress Log

## 2026-07-16 - Stripped recipes.db from git history, ahead of publishing to GitHub

User asked what was needed to publish this repo to GitHub, and whether
it contained any secrets. Audited before touching anything: no API keys/
tokens/passwords found anywhere in tracked files (pattern search plus
manual review); `llm_credentials.db` (the one place a real secret could
live) confirmed gitignored and never committed. One privacy-relevant
non-secret found: the `ollama_host` preference (in the git-tracked
`recipes.db`) held a real Tailscale IP - left as-is since `recipes.db`
itself was about to stop being tracked entirely (see below), making it
moot for the publishing question.

The actual blocker: `recipes.db` is ~500MB and had been committed 47
times across this project's history, making `.git` 6.5GB total - GitHub
hard-rejects any single file over 100MB, so a normal `git push` would
have failed outright, and simply gitignoring it going forward wouldn't
help since the file was already baked into 47 past commits. User chose
to have it stripped from history entirely rather than Git LFS or a
fresh no-history repo.

Executed via `git filter-repo --path recipes.db --invert-paths` against
a **fresh clone** (its own built-in safety requirement, and safer than
operating in-place regardless), not the working repo directly. Verified
the clone compiled and passed all 97 tests before touching the real
repo, then swapped its rewritten `.git` into the working directory
(old one moved aside, not deleted, until final verification passed).
Result: `.git` 6.5GB -> 57MB, `recipes.db` absent from `git log --all`
entirely, all commit hashes changed (expected/unavoidable for a history
rewrite), the live `recipes.db` file's checksum unchanged throughout
(confirmed twice - before and after - the actual data was never at risk,
only its git history). Added `recipes.db` to `.gitignore` - treated as
local/instance data now, same as `uploads/`/`llm_credentials.db`.

**Real incident during this, worth recording**: before running the
rewrite, made an extra safety-net copy of the full 6.5GB `.git` directory
into the session's scratch space under `/tmp` - which turned out to be a
small (7.5GB) tmpfs (RAM-backed) mount, not a large disk-backed
directory. Filling it to capacity caused nearly every subsequent shell
command to fail (SIGABRT/exit 1) for both the assistant and the user's
own direct terminal commands, including trivial ones like `echo`, until
the user independently confirmed via a separate tool that `/tmp` was the
culprit. No destructive git operation had been run yet at that point -
recovered by deleting the errant backup copy, then re-verified the real
repo/data were completely untouched (clean `git status`, correct HEAD,
matching `recipes.db` checksum) before proceeding. Redid the safety
strategy properly afterward: `git filter-repo`'s own fresh-clone
requirement on the 181GB main disk, not a manual full-directory copy
into a small RAM-backed mount.

Deleted the old pre-rewrite `.git` backup (6.7GB) after the user
explicitly confirmed, once the new history was fully verified working.

## 2026-07-16 - Fixed a recipe photo appearing twice on its own page

User-reported: viewing a recipe with a photo showed it twice - once as
the big banner, again as a small thumbnail right underneath.
Root-caused, not guessed: `recipes.image_url` is a denormalized cache of
`recipe_images` row 0 (`_sync_primary_image()`), and the gallery thumb
strip was rendering *every* `recipe_images` row including that same
first one - real duplication, not a rendering glitch. Fixed by having
the thumb strip only render rows 1+ (`gallery_images[1:]`), skipping
whichever one is already shown as the hero.

That created a real gap: the primary/hero photo would then have no
delete affordance at all if it's the *only* photo (true for ~all of
this corpus - checked: every recipe currently has exactly 1 image row).
Added a delete link on the hero itself (`.hero-delete-link`, same
underlying `DELETE /api/recipe/<id>/image/<id>` endpoint and
`refreshGallery()` flow as thumb deletion) rather than leaving a
single-photo recipe's only photo unmanageable.

Fixing this properly required more than a template tweak - the
existing `refreshGallery()` surgically patched individual DOM nodes
after an add/delete (update `.src`, or manually move a bare `<img>`
around relative to `.recipe-header`), which didn't account for the new
wrapper element or for the hero needing to flip between "photo" and
"no photo yet" states. Replaced with a full swap of whichever element
occupies that slot (`.recipe-hero-wrap` or `.no-photo-nudge`), and
moved the delete-link click handler from `#gallery-thumbs`-only to a
single document-level delegated listener - the previous per-element
binding would have silently gone dead the first time `refreshGallery()`
replaced the node it was bound to.

**Verified live against real data, not the fake test image URL I first
tried** (which triggered the *existing* `onerror` broken-image cleanup
and looked like a bug in my fix until I checked the raw HTML and
realized the URL itself never resolved): added a second real photo to
recipe 1226 via the live API, confirmed exactly 1 hero + 1 thumb with
different `src` values (no duplication), deleted the added photo via
the real UI delete flow, confirmed via the server-rendered HTML
(0 `<li>` elements) that it's gone and the original hero/photo count is
back to exactly 1. `recipe_images` for 1226 confirmed back to its
original single row afterward.

## 2026-07-16 - Home page redesign: smaller/deterministic hero, search first, easier categories, LLM transparency

Five pieces of user feedback on the home page and nav, addressed
together since they touched the same areas:

- **"Today's pick" hero was too big and wasn't actually "today"** - it
  used `ORDER BY RANDOM() LIMIT 1`, which re-picks on every single page
  load, not once a day, despite the label. Replaced
  `get_random_recipe_with_image()` with `get_recipe_of_the_day()`
  (`recipe_model.py`): deterministically seeded from today's UTC date
  (`sha256(date.today().isoformat())`), so it's the same recipe all day
  and a different one tomorrow - verified live by loading the home page
  twice and confirming the same title both times. Shrunk the hero from
  a 380px-tall banner + 2em title to a compact 56px-thumbnail row.
- **Search moved above the hero** (previously below it) - first thing
  on the page now.
- **Categories promoted to a top-level nav link** (previously the 5th
  item inside the "Discover" dropdown) - `Home | Search | Categories |
  Add▾ | Discover▾ | Plan▾ | You▾`.
- **LLM transparency on Discover items**: "Craving?" and "Invent" (the
  two Discover-tab features that actually call an LLM - "What Can I
  Make?" and "Pairings" don't) now show a small `LLM` badge in the nav
  dropdown itself, plus a same-badge-plus-sentence disclosure on the
  page when it loads ("Your text is sent to an LLM..." / "Writing the
  recipe itself is done by an LLM...", each linking to Preferences).

Verified live in headless Chrome: search box measured above the hero's
bounding box; hero title identical across two separate page loads;
Categories link present at the nav's top level; LLM badges present on
both dropdown entries and both destination pages. Full pytest suite
(97 tests) still passes; recipe count unchanged.

## 2026-07-16 - "Test Connection" button on the LLM Provider preferences

Direct user request, landed right after Ollama host became
UI-configurable: validate a provider/model/key/host *before* saving,
not just find out it's broken when a real feature fails later. New
`llm_client.test_connection(provider, model, api_key, ollama_host)` -
takes explicit values (not the saved/env-resolved config) so it
reflects exactly what's currently typed into the form, saved or not; a
blank api_key/ollama_host falls back to whatever's already configured,
matching the rest of the page's "blank means don't change" convention.
Sends one minimal real completion request (`max_tokens=10`, "Reply with
exactly one word: OK") and returns `(ok, message)` - never raises, since
the whole point is reporting failure as a result. New
`POST /api/preferences/test-llm` endpoint; a "Test Connection" button
(type="button", not type="submit" - must not trigger a save) shows
"Testing...", then a green success or red failure message inline.

**Verified with two real, distinct outcomes, not a mocked response**:
(1) tested the currently-unreachable Ollama default - genuine
`APIConnectionError`/DNS failure in ~0.1-0.3s; (2) typed an unsaved,
never-submitted fake Anthropic key into the form and clicked Test - the
request actually reached Anthropic's real API and came back with a
genuine `authentication_error`, and a reload afterward confirmed
nothing had been persisted (provider still showed the real saved value,
`ollama`). Both prove the button tests live, real, current form state -
not a cached or saved config.

## 2026-07-16 - Ollama host also UI-configurable (second follow-up to Phase 17)

Immediate follow-up to the API-key follow-up above: user asked "where do
I set up Ollama then?" after seeing API keys become UI-configurable -
Ollama has no API key, but it does need a reachable host/port, and that
was still env-var-only (`OLLAMA_HOST`), so the "configure everything
without a container restart" goal wasn't actually complete.

Unlike API keys, an Ollama host is a URL, not a secret - added as a
plain `ollama_host` column on the existing `preferences` singleton
(`recipes.db`, not `llm_credentials.db`), same "preserve on omit"
semantics and three-tier resolution priority (saved preference ->
`OLLAMA_HOST` env var -> hardcoded LAN default) as `llm_provider`/
`llm_model`. New `llm_client.get_ollama_host()`; `_model_string_and_kwargs()`
and `embed()` both switched from the old module-level `OLLAMA_API_BASE`
constant to calling it fresh per-request, so a saved change takes effect
immediately rather than only at process start.

Preferences UI: a "Ollama host" text field, shown only when Ollama is
the selected provider (parallel to how the API key field only shows for
providers that need one). Verified live: pointed the live preference at
a deliberately unreachable IP (`10.0.0.99`, nothing listens there on
this network) and confirmed the real request actually targeted it - a
genuine 90-second connection timeout, distinctly different from the old
default's fast connection-refused, proving the new value was really
used, not just accepted and ignored. Reverted to the default (blank ->
falls through to `OLLAMA_HOST` env var) afterward.

## 2026-07-16 - LLM provider API keys, also UI-configurable (follow-up to Phase 17)

User feedback right after shipping Phase 17: the Preferences page let
you *select* a provider but not *configure* it - Anthropic/Gemini still
needed an API key set via container env var + restart, exactly the
friction the whole feature was supposed to eliminate. Confirmed with the
user: no accounts/auth exist anywhere in Sous, so there's no "admin"
concept to gate this behind - whoever can reach `/preferences` already
has full control over everything else.

Added `llm_credentials.py`: a **separate** SQLite file
(`llm_credentials.db`, gitignored, same treatment as `uploads/`) rather
than a new `preferences` column, since `recipes.db` is git-tracked in
this project and a stored secret there would leak into git history the
first time anyone commits. `save_api_key(provider, '')` deletes rather
than storing empty, so the UI's "leave blank to keep what's saved"
semantics need no separate sentinel value.

`llm_client._resolve_api_key()` now prefers the UI-stored key over the
provider's env var when both are set, passed explicitly via
`litellm.completion(api_key=...)` rather than relying on litellm's own
env-var auto-read (which wouldn't have respected that priority).
`api_key_configured()` checks both sources.

Preferences UI: a password-type API key field appears only for a
provider that needs one, with a placeholder reflecting real state
("Not set yet" / "Already set - leave blank to keep") computed
server-side per provider - never re-displays a saved key's value, only
whether one exists. Saving updates the warning/placeholder immediately
client-side without a reload.

Hit a real deployment bug while testing under Docker: bind-mounting
`./llm_credentials.db` when that path doesn't exist yet on the host
makes Docker silently create a *directory* there instead of a file - no
error at container startup, but every `/preferences` request then fails
the moment anything tries to `sqlite3.connect()` a directory. Caught by
noticing `/preferences` alone returned `HTTP 000` while every other page
still worked (confirming the threading fix from earlier in this session
correctly contained the failure to one request). Fixed by pre-creating
the file (`touch`) before the bind mount ever happens - documented as a
required one-time setup step in `docker-compose.yml` and README, not
just noted in passing.

**Verified end-to-end with a real (fake) key**: saved a bogus Anthropic
key via the live UI, set the live provider preference to Anthropic, hit
a real LLM-backed endpoint (`/recipe/94874/easter-egg`) - the request
actually reached Anthropic's real API (a genuine ~300ms round trip) and
came back with `litellm.AuthenticationError: ... "invalid x-api-key"` -
not a "key missing" error, proof the key is really transmitted through
the whole chain (UI -> llm_credentials.db -> llm_client -> litellm ->
the real provider), not just accepted and silently dropped somewhere.
Reverted the live preference back to Ollama and cleared the test key
afterward - the household's real settings weren't left in a test state.

## 2026-07-16 - Pluggable LLM provider, UI-configurable (PLAN.md Phase 17, 3 of 3 requested features)

Closes PLAN.md Phase 17, opened after the original Ollama-connectivity
incident (2026-07-14) and made urgent by *this same session's* full-app
outage (see the "fixed a real full-app outage" entry above) - a second,
worse incident from the identical root cause (every LLM feature
hardwired to one Ollama host with no fallback), this time taking down
the entire server, not just the LLM features.

**Scope decisions, both confirmed with the user before building** (PLAN.md
left them open): litellm over a hand-written adapter (already a
dependency, least new code); and - going further than PLAN.md's original
env-var-only plan - **UI-configurable on `/preferences`**, not just env
vars. An env-var-only version wouldn't have actually fixed the problem
this phase exists to solve: switching providers would still mean editing
container env vars and restarting, the exact friction a live outage
doesn't have time for.

**New `llm_client.py`**: `chat()`/`chat_json()` (completions, JSON mode
via litellm's `response_format={"type": "json_object"}`, translated
per-provider) and `embed()` (Ollama-only regardless of active chat
provider - `ingredient_embeddings.vector`'s dimensionality is tied to the
specific embedding model already used corpus-wide, switching would need a
full re-embed of ~7,200 ingredients, out of scope). Provider/model
resolved in order: saved preference -> `SOUS_LLM_PROVIDER`/
`SOUS_LLM_MODEL` env vars -> the original Ollama/`qwen3:8b` default, so
nothing changes in behavior until someone opts in. API keys read only
from provider-standard env vars via litellm, never stored in
`preferences` - `recipes.db` is git-tracked in this project, so a stored
secret would leak into git history; this was a real design constraint
kept in mind throughout, not an afterthought.

**Migrated all 7 call sites** (`embeddings.py`, `flavor_tagging.py`,
`query_planner.py`, `pantry_shelf_life.py`, `recipe_adaptation.py`,
`recipe_invention.py`, `easter_egg.py`) off hand-rolled
`urllib.request`-to-`OLLAMA_HOST` calls (5 of them had byte-for-byte
identical request-building code, confirmed before writing the shared
wrapper). `easter_egg.py` previously had its own independent
`SOUS_EASTER_EGG_MODEL` env var and lazy `litellm` import (litellm was
optional, only needed for that one feature) - now unified onto the same
shared setting as everything else, a deliberate behavior change: one
provider for the whole app, not a per-feature override. `litellm` is
consequently no longer lazily imported anywhere and is now a hard
requirement (`requirements.txt` comment updated) - it was already
unconditionally installed in every real deployment via
`pip install -r requirements.txt` regardless, so this changes documented
intent, not actual runtime behavior.

Added `llm_provider`/`llm_model` to the `preferences` singleton
(migration-safe, same "preserve on omit" semantics as
`hide_builtin_recipes` - the v1 API's `_api_update_preferences` doesn't
know about these fields either). New "LLM Provider" section on
`/preferences`: provider dropdown (Ollama/Anthropic/Gemini), model text
field (auto-fills that provider's default when switching, but only if
the field still holds some *other* provider's untouched default - never
overwrites a real custom model the household typed in), and a live
warning if the selected provider needs an API key that isn't set in the
container environment - computed server-side per provider
(`llm_client.api_key_configured()`) and checked client-side on selection
change, no round trip.

**Verified live, every layer, not just unit-level:**
- Provider-resolution logic (preference wins, env fallback, unrelated
  saves don't reset it) - direct calls against a scratch DB, no network.
- `chat()`/`chat_json()`/`embed()` against the *real* unreachable Ollama
  host (still down all session): all three fail in ~3s with a clear
  `LLMUnavailableError`, not a hang - meaningfully faster and safer now
  that the server is threaded too (one slow/failed call no longer blocks
  concurrent users regardless).
- Preferences UI in headless Chrome: provider switch auto-fills the
  model field and shows/hides the API-key warning correctly; save
  persists across reload; reverted cleanly back to Ollama.
- **End-to-end through a real feature**: set the live preference to
  Anthropic via the real API (no `ANTHROPIC_API_KEY` in this container),
  hit `/recipe/94874/easter-egg`, got back the exact expected chain -
  `llm_client.chat()` -> `easter_egg.py`'s `RuntimeError` wrapper ->
  the recipe page's existing error display - in 9ms (fails before
  attempting any network call, since the missing-key check runs first).
  Confirms the full path from Preferences through to real feature code
  is wired correctly, not just that the pieces compile.
- Reverted the live preference back to Ollama afterward - the household's
  actual setting wasn't left in a test state.

Not done, deliberately deferred (see PLAN.md): native structured outputs
for Claude (`client.messages.parse()`) - kept the uniform JSON-mode
pattern across all providers instead of a Claude-specific code path.

## 2026-07-16 - Recipe edit UI (2 of 3 requested features)

`PUT /api/recipe/<id>` (`handle_update_recipe()`) already existed but had
no page of its own - only used internally (e.g. by the photo-gallery
JS's primary-image sync). Added `GET /recipe/<id>/edit`
(`serve_edit_recipe()`), a form pre-filled from the existing recipe
covering every content field the backend already accepts (title,
description, ingredients, instructions, prep/cook/total time, servings,
cuisine, difficulty) - deliberately excludes `url`/`license`/`image_url`,
since those are provenance or already have their own dedicated UI (the
photo gallery), not recipe content. Linked from a new "Edit Recipe"
button next to Print/Comedic riff on the recipe page.

Verified live end-to-end against a real scratch recipe (created via the
API, not a production row): pre-fill correct, edited every field,
saved, confirmed the *actual recipe page* re-rendered with every new
value (title, ingredients), then deleted the scratch recipe. Caught and
cleaned up an unrelated leftover from an *earlier* verification step
this session (dispatching a synthetic `submit` event at the live-search
quick-add form to confirm its listener re-wires after DOM injection had
a real side effect - it actually created a junk recipe via the real
`POST /api/recipe` call the handler makes - "zzzznonexistentrecipezzz",
id 95416) - deleted via the real DELETE endpoint, confirmed back to the
correct 54,722 baseline afterward.

## 2026-07-16 - Nudge to add a photo when a recipe has none (1 of 3 requested features)

First of three features requested this session (sequenced smallest-first:
photo nudge, edit UI, LLM provider config). The photo-management UI
(add by URL/upload, gallery, remove) already existed in full - it was
just invisible when a recipe had zero photos (blank space where the hero
image would go) and hidden behind a small "Manage photos" toggle
otherwise. Added a visible `.no-photo-nudge` prompt in that empty slot
("This recipe has no photo yet. Add one") whose link reveals the
existing add-photo controls and focuses the URL input - reused the
existing `togglePhotoEditing()`/gallery machinery rather than building a
second add-photo path. Verified live: clicking "Add one" on a recipe
with no photo (94876, "Apple and Dried Cranberry Pie") reveals the
controls and focuses the right input; screenshotted.

## 2026-07-16 - Live search-as-you-type; fixed every checkbox on the site being stretched full-width

**Live search.** The `/search` page now updates results as you type
(300ms debounce), not just on submit. Refactored `serve_search()`'s
results/heading building into `_search_results_and_heading()`, shared
with a new `serve_search_fragment()` (`GET /api/search/fragment?q=...`)
that returns the same rendering as pre-built HTML snippets for the client
to drop into the DOM - the same pattern this codebase already uses for
`/api/plan/<id>/fragment`, rather than reconstructing markup from raw
JSON client-side. The client uses `Range.createContextualFragment()`
(not `innerHTML`) to inject the response, specifically because the empty
"no results" state embeds the quick-add form's own `<script>` tag
(`get_add_recipe_form_html()`) - `innerHTML`-inserted `<script>` tags are
inert in every browser, which would have silently broken quick-add for
any zero-result live-search state. Verified live in headless Chrome,
including that the injected quick-add form's submit handler genuinely
re-wires itself (dispatched a synthetic submit event and confirmed
`preventDefault()` fired). Stale-response race handled with a
request-id counter (a fast keystroke firing while an older fetch is
still in flight no longer lets the older response clobber newer results).
URL kept in sync via `history.replaceState` (not `pushState` - typing
shouldn't spam browser back-history) so results stay bookmarkable/
shareable.

**Fixed every checkbox on the site rendering full-width, stacked away
from its label text** - reported on the hide-built-in-recipes checkbox,
but root-caused to a global `input, textarea, select { width: 100%; }`
rule in `get_base_style()` that was inadvertently also stretching every
`input[type="checkbox"]` on the site (shopping-list item checks, the
print view's "Show image"/"Show nutrition" toggles, and this one).
Fixed at the rule, not per-instance: added `input[type="checkbox"] {
width: auto; }` plus a `.checkbox-label` flex/gap treatment for the
preferences checkbox specifically. Screenshotted to confirm.

## 2026-07-16 - Grouped ingredient sections, fixed a real full-app outage, fixed a search dead-end

Three unrelated fixes from one session, logged together since they landed
back to back.

**Grouped ingredient sections** (the original ask, from recipe 94874
"Alsatian Apple Cake" having a crust + filling): some source recipes
embed component labels ("For the Crust:", "Filling:") as plain entries in
their ingredients list rather than real ingredients. Previously these got
fed straight into `parse_ingredient()`'s ML parser, which produced
inconsistent garbage (`name="For the Crust"` on one row, nothing at all
on the next for the same kind of line) and rendered as an indistinguishable
bullet on the recipe page. Added `is_ingredient_section_header()`
(`recipe_scaling.py`) - a fast deterministic check (ends with `:`, no
leading quantity), run *before* the ML parse rather than trying to infer
header-ness from its output. Verified zero false positives against every
distinct `:`-terminated line already in the corpus (198 lines eyeballed
by hand) plus a dedicated unit-test class. Added `recipe_ingredients
.is_section_header` (migration-safe `ALTER TABLE`), skip the ML parse
entirely for header rows in `_sync_structured_ingredients()`, and render
them as headings instead of bullets (`server.py`'s new
`ingredients_list_html()`, shared by the recipe page and print view).
Backfilled the existing 517,392 rows via `backfill_section_headers.py`
(fast - no ML calls, single pass, <1s) - 202 rows marked (more than the
198 found by eyeball, since the deterministic check catches edge cases
the ML parser's inconsistent output on garbage input didn't). Also caught
and fixed a second real consumer: `shopping_list.add_recipe_to_list()`
would have added "For the Crust:" as a line item to buy - now skips
`is_section_header` rows. No recipe design (linked/separate recipes vs.
grouped sections) - grouped sections matches how basically every recipe
site handles composite dishes and the labels were already latent in the
data, just not recognized as such. Verified live: recipe 94874 renders
"For the Crust:"/"For the Filling:" as bold headings with ingredients
correctly grouped underneath (screenshotted), on both the recipe page and
print view.

**Fixed a real full-app outage.** While testing the above, the entire app
went unresponsive - every page, not just one. Root cause: `run_server()`
used a plain `socketserver.TCPServer` (single-threaded - one request at a
time), and the household's LAN Ollama host was unreachable at the time,
so whichever LLM-backed request came in (craving/adapt/easter-egg all
have 30-120s `urllib` timeouts) blocked the *entire server* until it
timed out - confirmed via `/proc/1/wchan` showing the process blocked in
`poll_schedule_timeout` and CPU at ~0%, not spinning. Fixed by switching
to a new `ThreadingRecipeServer` (`ThreadingMixIn` + `TCPServer`,
`daemon_threads=True`) - safe with no other changes since every DB call
already opens its own short-lived SQLite connection and there's no
shared/global mutable state anywhere in `server.py`. Since this made
genuinely concurrent requests possible for the first time (previously
structurally impossible under strict serialization), also switched
`recipes.db` to WAL mode (`PRAGMA journal_mode=WAL` in
`RecipeDatabase.init_database()`) so a concurrent writer can't block
readers - the default rollback-journal mode takes an exclusive lock for
the whole write, which was never a real risk before this session.
Verified live: 10 concurrent home-page requests completed in ~0.35s total
(not serialized 10x), and the full pytest suite (97 tests after this
session's additions) still passes.

**Fixed a search dead-end.** The nav's "Search" link goes to bare
`/search` (no query), which `serve_search()` deliberately renders as
"Browse all recipes" for browsing - but that page had no search input of
its own, only the home page did. Clicking "Search" from anywhere but home
stranded you on a full recipe listing with no way to actually type a
query. Added the same search box to `/search` itself, pre-filled with the
current query when there is one. Verified live: navigating directly to
`/search` now shows an empty, usable search box; searching from there
round-trips correctly with the box showing the submitted query.

## 2026-07-16 - Recovered missing photos on all 555 imported recipes (unplanned, user-reported)

Surfaced by the hide-built-in-recipes toggle: with it on, the user's own
555 recipes showed zero images. Root cause turned out to be two separate,
compounding things, both confirmed against real data rather than assumed:

1. `import_paprika_file()` never read `photo_data`/`image_url` from the
   Paprika export at all - verified by inspecting the real export
   (`~/Downloads/Export 2026-07-16 11.02.50 All Recipes.paprikarecipes`,
   the household's actual Paprika library, 555 entries): 502 recipes have
   an embedded `photo_data` (base64 JPEG, Paprika's locally-stored photo)
   and 502 have an `image_url` (original web link), 506 have at least
   one. None of it was ever captured.
2. The uploaded file itself was never the recoverable copy -
   `handle_import_paprika` writes it to a temp path and deletes it
   immediately after import (`server.py`), so the only way to recover the
   photos was asking the user for the original export file again.

Fixed `import_paprika.py` for future imports: captures the photo via the
existing `recipe_images`/`uploads` infrastructure (already used by the
manual "add a photo" feature - reused as-is, not reimplemented),
preferring `photo_data` over `image_url` since a locally-stored photo
doesn't rot like an external link.

Recovered the existing 555 via a new one-off `backfill_paprika_photos.py`,
matched by exact `(title, url)` against the already-imported rows -
verified 1:1 clean (555 file entries / 555 db rows / 0 orphans either
way) before running. Result: 502 photos saved locally via the real
`uploads.save_upload()` path (real magic-byte validation, not just
copied), 1 recipe fell back to its `image_url`, 48 had neither in the
source (a real gap in the original Paprika library, not a bug - left as
no image rather than fabricating one). Caught and fixed a second-order
bug in the backfill itself: 4 recipes are genuine duplicates within the
household's own Paprika library (identical title+url appearing twice) -
the naive match pointed both file entries at the same db row, leaving
one twin imageless. Fixed by copying the image across each duplicate
pair directly.

**Verified live** in headless Chrome (not just DB counts): home page
with the toggle still on (the user's real, current setting - left as
they had it) went from 0 real thumbnails / 24 placeholders to 24 real
thumbnails / 0 placeholders on the first page, confirmed one image
actually decoded (`naturalWidth: 280`, not a broken `<img>`), and a
recipe detail page rendered its photo too. `uploads/` now holds 502 new
files (14MB, gitignored as before - not committed, same as any other
uploaded photo).

## 2026-07-16 - Added "hide built-in recipes" preference (unplanned, user request)

User wants an optional way to browse only their own added recipes, hiding
the bulk-imported corpus. Scoped this before building: the only reliable
signal for "mine vs. built-in" is `license = 'user-imported'`, but that
tag turned out to only be set by the two bulk-import paths
(`import_paprika.py`, `import_bulk.py`) - manual `/add` and single-URL
import (`import_url_recipe.py`) both left `license` blank (confirmed via
two real examples: "Banana Bread" and "Chewy Chocolate Chip Cookies" from
2026-07-08). Fixed all four Recipe-construction call sites (`server.py`'s
`handle_create_recipe`/`_api_create_recipe`, and
`import_url_recipe.py:import_recipe_from_url`) to tag `user-imported` too,
so the new toggle actually means what it says for recipes added going
forward.

Added `hide_builtin_recipes` to the `preferences` singleton
(`preferences.py`, migration-safe `ALTER TABLE` for the existing table).
Unlike the other three preference fields (full-replace, clear when
omitted), this one preserves its stored value when omitted from a save
call - the v1 API's `_api_update_preferences` saves the other three
without knowing about this field, and full-replace semantics there would
have silently flipped the display filter off on every unrelated
preferences update.

Threaded `exclude_builtin` through every recipe-listing surface, not just
the home page - `get_all_recipes`/`count_recipes`/`get_random_recipe_with_image`/
`search_recipes`/`count_search_results`/`find_recipes_by_ingredients`
(`recipe_model.py`), `get_recipes_by_category`/new `get_category_counts()`
(`categories.py`, added the category-index page since an unfiltered count
there would show a category as nonzero and then render empty once
clicked into with the toggle on), and both the HTML routes and their
`/api/v1/*` REST equivalents in `server.py` (home, search, categories
index + per-category, discover, craving's fallback search). Left the
craving feature's primary flavor-index path (`recipe_flavor_index.py`)
unfiltered - it's a recommendation engine over precomputed flavor stats,
not a literal recipe list, and out of scope for this request.

**Verified live** via a real headless-Chrome/Playwright run against the
rebuilt image (all five source files also `py_compile`-clean first):
checkbox state persists across reloads; home page goes from 54,722 to
exactly 555 recipes with the toggle on (matching `license='user-imported'`
count exactly) and back to 54,722 off; `/category/Instant%20Pot` (a
100%-user-recipe category) renders exactly 68 cards both ways; browse-all
search shows 551 (4 fewer than 555, because search's pre-existing
"has instructions" filter excludes 4 of the user's own recipes -
expected, not a regression). Left the preference in its default off
state before committing.

This wasn't a PLAN.md item - logged here for the same reason as the
import-page fix above: real shipped code, not a phase advancement.

## 2026-07-16 - Fixed silent-hang bug in the import page (unplanned, data-integrity incident)

User-reported: the Paprika bulk-import form on `/import` gave zero visual
feedback while a POST was in flight (no spinner, no disabled button, no
"importing..." state), so a real-world import of a 555-recipe Paprika
export looked hung and got double-clicked. Server logs confirmed two
successful `POST /api/recipe/import/paprika` calls 20s apart followed by
three 400s from further clicks - exactly the double-submit signature.

Net effect on `recipes.db` before it was caught: the same 555-recipe file
had already been imported once on 2026-07-09, then today's double-submit
added it twice more (1,110 rows, 543 distinct titles - some 2x, a few
4x/6x from the double-submit itself layered on titles that were already
duplicated within the source file). Root-caused entirely from the
container's access logs and `created_at` timestamps - no guessing.

Fix was structural, not a one-off cleanup: `recipes.db` was untouched at
commit `7e09388` (54,722 rows, including one correct copy of the 555)
right up until today's import, so `git checkout -- recipes.db` reverted
exactly the erroneous rows and nothing else - confirmed by row count
(54,722) and a clean `git status` afterward. No manual dedup/DELETE
needed.

Fixed the actual bug in `serve_import_page()`'s inline JS (all three
forms - URL import, Paprika import, bulk JSON import): submit button now
shows "Importing..." and disables itself immediately on submit, only
re-enabling on failure; added `.catch()`/`reader.onerror` handling so a
network error surfaces instead of hanging silently forever.

**Verified live, not just read from source** - rebuilt the `sous:local`
image (server.py is baked in, not bind-mounted) and recreated the
container, then drove a real headless Chrome instance (Playwright)
against `/import`: attached the `test_data/sample.paprikarecipes` fixture,
clicked the submit button, confirmed via the live DOM that it read
"Importing..." and `disabled=true` immediately, then fired a second
forced click on top of it. `recipes.db` went from 54,722 -> 54,724 (+2,
matching the 2-recipe fixture exactly) - not +4. A real disabled
`<button>` doesn't dispatch click/submit handlers in Chrome, so the
second click was structurally inert, not just slow. Cleaned up the 2 test
rows via the same `git checkout -- recipes.db` revert before committing.

This wasn't a PLAN.md item - logged here because it's a real fix to
shipped code and a real (now-resolved) data-integrity incident, not
because it advances Phase 15/17.

## 2026-07-14 - Phase 15 (Corpus & Usefulness Strategy) worked through

Tackled Phase 15 next. Started by asking for the exact source data behind
the "Malaysian Chicken Curry" broken-recipe example from the UX review -
turned out not to be a Sous bug at all: the source dataset's own
`ingredients`/`instructions` fields were already `["Chicken Drumsticks",
...]`/`[]`, and `datahiveai/recipes-with-nutrition` (SPEC.md) was
documented at import time as "ingredients + nutrition only, not full
step-by-step recipes" - the gap was known and named from the start, not
discovered now.

Broke the 36.7%/59.4% headline numbers down by batch: 32,480 of 32,481
"both missing" recipes (99.997%) fall inside that one CC-BY-NC-4.0 batch -
82% of it is incomplete. The single outlier (recipe 12085) is an unrelated
broken import row (`ingredients: [""]`), not part of any pattern.

Decided the product stance: exclude incomplete recipes (no real
instructions) from search/browse by default rather than rank them lower.
They still feed the flavor/pairing pipeline unaffected, since that only
ever used ingredient names, never quantities. Backfill/generation was
explicitly ruled out - inventing instructions from a bare ingredient list
is content generation, not gap-filling, with real hallucination risk.

Shipped the completeness signal - turned out **not** to need new
infrastructure, just wiring existing infrastructure into one more place.
`completeness_score` and an `instructions IS NOT NULL` filter already
existed (2026-07-12) and were already used by `find_recipes_by_ingredients()`
and the flavor-discovery index; `search_recipes()`/`count_search_results()`
(plain keyword search - the exact path that surfaced the broken "chicken
curry" result) was the one place missing it. Fixed: both the FTS/bm25 and
LIKE-fallback paths now filter and tiebreak the same way. Verified live:
"chicken curry" no longer surfaces the broken recipe; `/search` with no
query dropped from 54,722 to 21,231 recipes shown by default (home page's
own recents list is a separate code path, unaffected).

Split `preparation` out as its own `recipe_ingredients` column (was folded
into `name`, e.g. "diced onion" instead of name="onion" +
preparation="diced") - `ingredient-parser-nlp` already extracted this as a
labeled span, it just wasn't being kept. Additive, no downstream consumer
needed changes. Backfilled all 517,392 existing rows via
`reparse_ingredients_nlp.py` (~26min).

Mid-discussion, walked through whether the canonical `Recipe`
model (free-text `ingredients`/`instructions` lists, with a derived
structured `recipe_ingredients` table) was "too loose." Landed on: parsing
already happens once, eagerly, at write time (not on-the-fly, not
per-request) - no disagreement there. What surfaced instead was a real,
narrower bug: the recipe detail page's default/print views rendered
ingredients from raw text while the *scaled* view rendered from the
structured table, so the same recipe could show two different-looking
ingredient lists depending on whether `?servings=` was set. Fixed - both
views now always render through `scale_recipe_to_servings_structured()`
(factor=1.0 when not scaling), one path instead of two. Kept raw text as
the storage-layer source of truth (that's what makes reparsing possible
when the parser improves, and what manual entry has to start from) - only
the *consumption* side needed unifying.

Researched replacement/supplement corpora for the incomplete CC-BY-NC-4.0
batch: RecipeNLG (1M+, non-commercial license, requires a manual
click-through download - not scriptable), Recipe1M+ (MIT-affiliated,
license unclear, needs direct verification), and RecipeNLG Lite
(`m3hrdadfi/recipe_nlg_lite`, MIT, 7,198 recipes, directly downloadable).
Pulled and inspected RecipeNLG Lite directly: 0% empty ingredients/steps,
96%+ real quantities, 6.4% title-overlap with the existing corpus
(~6,734 net-new). Wrote `import_recipe_nlg_lite.py` (reuses the existing
`Recipe`/`save_recipe()` path, no new parsing logic - splits the source's
comma-joined ingredients/steps into lines and lets the existing NLP
parser handle them) - not yet run, pending go-ahead. Further, larger
corpora still to evaluate per the user's request.

See PLAN.md Phase 15 for the full itemized breakdown.

## 2026-07-14 - Phase 16 (Polish) knocked out

User asked to tackle Phase 16 first, of the three phases opened from
the UX review. All three items fixed, verified live, tests re-run
clean:

- Empty-query `/search` now heads "Browse all recipes" instead of
  `Search Results for ""`.
- Manual `/add` form gained servings + cuisine fields (the backend
  already accepted both - `handle_create_recipe` defaults them to 1/'',
  so this was purely a missing-inputs gap) plus placeholder example
  text on the ingredients/instructions textareas. Verified end-to-end
  by actually submitting a recipe through the real form (servings=12,
  cuisine=american), confirming both landed in `recipes.db` via direct
  SQL, then deleting it through the real DELETE endpoint and confirming
  the cascade left no stray rows in `recipe_ingredients`/`recipe_steps`/
  `recipe_categories`.
- Recipe page photo controls (add-by-URL, upload, per-thumbnail remove)
  are no longer shown by default - they're behind a "Manage photos"
  toggle now (`.gallery.editing` class flip in CSS + JS). Confirmed the
  2026-07-13 `refreshGallery()` fix still works unmodified, since it
  only touches `#gallery-thumbs`/`.recipe-hero-image`, neither of which
  moved.

Rebuilt the Docker image, recreated `sous-local` (same `OLLAMA_HOST`
fix from earlier today carried forward), and drove all three flows
live via headless-Chrome CDP before calling this done. Full 91-test
suite re-run clean (`.venv/bin/python -m pytest tests/ -q`). See
PLAN.md Phase 16, MISSING_FEATURES.md.

## 2026-07-14 - Opened Phases 14-16, fixed Ollama connectivity

Turned the UX review's findings into tracked work: three new PLAN.md
phases (14 Documentation, 15 Corpus & Usefulness Strategy, 16 Polish),
each with concrete unchecked items pulled from the review.

Also fixed one of the two headline findings same-day: `OLLAMA_HOST`
was unset in the running container, so the LLM query planner behind
"Craving?" could never reach Ollama. User pointed at the
`update_ollama_host` shell function to get the correct value
(`http://ollama-interface:11434`, a Tailscale MagicDNS name). The
hostname doesn't resolve inside the container's bridge network -
confirmed via `docker exec sous-local getent hosts ollama-interface`
failing - but the underlying Tailscale IP (`100.64.4.80`) is reachable
from inside the container (`urllib.request` to `:11434/api/tags`
succeeded). Recreated `sous-local` with `OLLAMA_HOST=http://100.64.4.80:11434`,
same port mapping and bind mounts as before (no data loss - `recipes.db`
and `uploads/` live on the host, not in the container). Verified live:
`/craving?q=cozy rainy day soup` now returns "Interpreted as: umami,
warm spice" instead of the keyword-search fallback. The IP isn't
permanent - re-run `update_ollama_host` and re-pass it if the container
is ever recreated again. See MISSING_FEATURES.md, PLAN.md Phase 15.

## 2026-07-14 - Adversarial novice-user UX review

User asked for an adversarial review as a first-time, non-technical
user: documentation quality, usefulness, ease of use, and missing
features vs. Paprika/Tandoor. Done by actually driving the live
`sous-local` container (port 8130) through headless Chrome via CDP -
home, search (real + no-results + empty-query), a recipe detail page,
`/add`, `/import`, `/discover`, `/craving`, `/pairings`, `/plans`,
`/lists`, `/pantry`, `/preferences` - not just reading code, per this
project's standing "actually try it in the browser" practice.

Two substantive findings, both added to `MISSING_FEATURES.md`:

- **Corpus completeness**: verified via direct SQL that only ~36.7%
  of the 54,722 recipes have both real instructions and at least one
  parsed ingredient quantity - 59.4% have neither. This isn't a Sous
  bug (the source datasets are genuinely incomplete for these rows),
  but search/discovery surfaces these recipes with no visual signal
  of incompleteness, and the literal top hit for "chicken curry" is
  one of them (ingredients with no quantities, instructions field
  empty). Previously only the *image*-coverage gap was documented,
  not this larger instructions/quantity gap.
- **LLM features are dark in this deployment**: `docker exec sous-local
  env | grep -i ollama` shows `OLLAMA_HOST` isn't set in the
  container at all, so it defaults to the container's own loopback
  and can never reach the host's real Ollama instance. Confirmed live:
  `/craving?q=cozy rainy day soup` fell back to plain keyword search
  and returned zero results. This silently degrades "Craving?" and
  "Adapt for my preferences" to non-functional in the currently
  running container - an easy fix (pass `OLLAMA_HOST` at `docker run`
  time), not yet applied since it wasn't clear the user wanted the
  container reconfigured mid-review.

Also fixed a stale doc bug found along the way: README's "Known
limitations" test-coverage bullet still described only the original 3
areas after the flavor/scaling suite expanded it to 5 (91 tests) on
2026-07-13.

Full findings (including minor UX papercuts) reported directly to the
user rather than only logged here.

## 2026-07-13 - Test coverage expanded to recipe scaling + flavor engines

With no urgent gaps left in `MISSING_FEATURES.md`, asked what's next;
offered three options (expand test coverage, per-step image
association, external flavor-taxonomy validation) and recommended the
first since the existing suite was deliberately scoped to only three
areas and the flavor/scaling engines - real computational complexity
backing several user-facing features - had zero regression protection.
User agreed.

Added 59 tests across three new files, following the existing
`conftest.py` conventions (throwaway SQLite per test via `tmp_path`,
schema built through the app's own init functions, `make_recipe()`/
`set_ingredient_quantities()` helpers):
- `test_recipe_scaling.py` - the raw-text and structured-ingredient
  quantity parser/formatter/scaler, exercising documented edge cases
  (unicode fractions, mixed numbers, ranges, unit-less lines, irregular
  plurals) rather than just the happy path. Pure functions, no DB.
- `test_flavor_queries.py` - ingredient/recipe/cuisine/meal-plan flavor
  aggregation, with a direct regression test pinning this session's
  earlier 70bbde8 fix (must key off `recipe_ingredients.name`, not raw
  ingredient text).
- `test_recipe_flavor_index.py` - the `recipe_flavors` precomputed index
  build and `find_recipes_by_flavors()`'s ranking/cuisine/max-time
  filters and its "must have real instructions" requirement.

**Found and fixed one real latent bug while writing these tests**:
`get_cuisine_flavor_profile()` lowercases the incoming query
(`target = cuisine.strip().lower()`) but compared it against
un-lowercased comma-split cuisine components pulled straight from the
DB. The SQL `LIKE` prefilter is case-insensitive, so a differently-cased
cuisine value would pass the prefilter and then silently fail the exact
membership check - it only ever "worked" because the real corpus happens
to store every cuisine value already lowercase, not because the code
guaranteed it. Fixed by lowercasing the components too. This function
isn't wired to any HTTP route yet (an internal query building block, not
yet surfaced), so verification was direct: imported it against the real
`recipes.db` and confirmed `get_cuisine_flavor_profile("italian")` and
`get_cuisine_flavor_profile("ITALIAN")` now both return the same 50
recipes.

All 91 tests pass; syntax-checked under Python 3.11 (Docker's version)
per this session's standing practice, then rebuilt into the Docker image
and confirmed the container starts clean and serves real requests before
committing (`9f2c02b`).

## 2026-07-13 - Re-score against the Useful/Elegant/Beautiful/Accessible/
Trustworthy/Innovative rubric, then fix what it found

User asked for an honest re-grade against the same rubric used in the
2026-07-11 self-assessment (same six categories), now that shopping
lists, photos, pantry, the test suite, the public API, and `/add` had
all shipped since. Found the overall picture improved, but two concrete
regressions/gaps rather than just re-confirming prior scores:

1. **Elegant regression**: the photo-delete handler (added this session,
   for multi-photo support) called `location.reload()` after its own
   `fetch()`-based delete, silently reintroducing the exact
   full-page-reload pattern the app had already eliminated everywhere
   else (toggle/delete/add actions elsewhere all update the DOM in place
   without a round-trip). A regression, not a pre-existing gap - worth
   fixing on its own rather than waiting for the next full pass.
2. **Beautiful/IA gap**: the nav bar had grown to 14 flat links wrapping
   to two lines with zero hierarchy, as Pantry/Shopping Lists/Add Recipe/
   Preferences/History all landed as flat top-level items over the
   session with no grouping pass ever applied.

User approved fixing both ("yes - fix them please").

**Fix 1**: `refreshGallery()`'s existing DOM-patch-in-place approach was
extended to also sync the hero image (a separate element outside
`#gallery-thumbs`, so it needed its own handling for three cases: hero
src changes, hero disappears when the last photo is removed, hero
appears when the first photo is added) instead of falling back to
`location.reload()` to get the hero in sync. The delete handler now
just calls `refreshGallery()`.

**Fix 2**: `get_nav_html()` regrouped into four `<details>/<summary>`
dropdowns - Add (Add Recipe, Import), Discover (What Can I Make?,
Craving?, Pairings, Invent, Categories), Plan (Meal Plans, Shopping
Lists, Pantry), You (Preferences, Cooking History) - with Home/Search
left as flat top-level links. Chose `<details>` specifically because
it's natively keyboard-accessible (Enter/Space toggles, normal tab
order) and functions with zero JS; a small script on top adds "only one
dropdown open at a time" and "closes on outside click," but neither
behavior is required for the nav to work.

**Bug found and fixed along the way**: the down-caret CSS
(`content: ' \25BE';`) was written inside a plain (non-raw) Python
triple-quoted string. Python's own string-literal parser consumed
`\25` as an octal escape sequence before the string ever reached the
browser, so the served CSS had a stray control character followed by
literal `BE` - rendered as garbled text instead of a caret. Found by
comparing a screenshot against the expected glyph, confirmed via
`repr()` of the actual generated string in a REPL, fixed by escaping
to `\\25BE`.

**Verification honesty note - a real mistake made and fixed during this
verification pass**: per the user's earlier explicit correction ("actually
try it out in the browser"), both fixes were verified via real CDP-driven
Chrome interaction, not curl. While testing the photo-delete fix, the test
script added a second photo to recipe 1226 and then clicked
`document.querySelector('.delete-image-link')` to confirm no-reload
behavior - but `querySelector` matches the *first* element in DOM order,
which was recipe 1226's original corpus photo, not the newly-added test
photo appended after it. This deleted the real photo. Compounding it: a
subsequent "cleanup" delete of what was assumed to be the leftover test
image was actually the last remaining image, leaving recipe 1226 with zero
photos and an empty `recipes.image_url`. Caught immediately via direct
SQLite inspection (`recipe_images` empty, `recipes.image_url = ''`) rather
than assumed-clean. Repaired via `POST /api/recipe/1226/image` with the
original image URL (recalled from having appeared in multiple earlier
screenshots of this same recipe this session), then re-verified via direct
SQL that exactly one clean row exists and `recipes.image_url` is correctly
synced back to it before committing anything. Recorded here rather than
omitted, matching this project's practice of logging real mistakes made
during verification, not just successes.

Reran the full test suite (32/32 still passing - neither fix touches
logic the suite covers, but cheap to confirm) and swept `git status`/SQL
for any other stray test artifacts before committing. Both fixes
committed together (`f48c40e`).

## 2026-07-13 - Direct "Add a Recipe" page

User asked how easy it is to add a new recipe manually. Checked the code
rather than answer from memory: URL import and bulk import (Paprika/JSON)
both work fine, but the one manual-entry path (title + ingredients +
instructions, posting to the existing `POST /api/recipe`) only existed
embedded in `/search`'s empty-results state - there was no direct,
discoverable way to reach it; you had to search for a title that didn't
exist yet. Flagged this honestly as a real small gap and asked if it was
worth fixing; user said yes.

Extracted the form (previously duplicated nowhere, but about to be
needed in two places) into a shared `get_add_recipe_form_html()` used by
both the existing empty-search quick-add box and a new standalone `/add`
page with its own nav link - no new backend logic, same `POST
/api/recipe` endpoint both paths already used. Verified the extraction
didn't regress the original empty-search path (searched a nonsense query,
confirmed the quick-add box still renders and still works), verified
`/add` itself creates a real, retrievable recipe, both locally and again
against the rebuilt container, and reran the full test suite (still 32/32
- this change doesn't touch anything the suite covers, but cheap to
confirm). Test recipes deleted after verification.

## 2026-07-13 - Automated test suite (targeted, not exhaustive)

With every item from the Tandoor/Paprika feature-parity gap analysis now
closed (shopping lists, photos, pantry, public API), asked what's left;
the only remaining item the user had explicitly flagged as a real
priority (not just a nice-to-have) was the 2026-07-10 note: "no
automated test suite... something to actually build." Proposed scoping
it to the three highest-risk pieces of business logic rather than
attempting exhaustive coverage in one pass - shopping-list quantity
merging (already produced one real bug this session), pantry shelf-life
decay/confirmation thresholds, and the backward-scheduling conflict
detector. User agreed.

`tests/conftest.py` gives every test its own throwaway SQLite file under
pytest's `tmp_path`, with schema built the same way the app itself
builds it (`RecipeDatabase`/`MealPlanDatabase`/each module's own
`init_*_table()`) rather than hand-rolled `CREATE TABLE` statements that
could drift from the real schema unnoticed. Recipes go through the real
`save_recipe()` path (real FTS sync, real structured-ingredient sync),
with a `set_ingredient_quantities()` helper that then directly overwrites
the parsed quantity/unit/name - this decouples "does the merge/
scheduling logic work" from "is the NLP ingredient parser accurate,"
which is a separately-already-validated concern.

32 tests across three files, all passing:
- `test_shopping_list.py` - includes a direct regression test for the
  actual bug the shopping-list feature shipped with and fixed this
  session (merging into an existing NULL-quantity row raised a
  TypeError).
- `test_pantry.py` - the 0.8x/1.5x shelf-life thresholds via backdated
  synthetic rows, and a direct test that `get_confirmed_fresh_names()`
  really does exclude `needs_confirmation` items (not just "intended to"
  - the exact guarantee `/discover`'s pantry auto-fill depends on).
- `test_scheduling.py` - real `extract_step_duration`/`classify_step_type`
  parsing, and conflict-detector cases (overlapping active flagged,
  overlapping passive not, non-overlapping active not, a recipe added
  twice to the same plan never flagged as conflicting with itself).

**Two of the first-draft tests failed on the first run - correctly**,
not because of app bugs but because of arithmetic mistakes in how the
tests constructed their own fixture data (a sort-key that put `None`
after `2.0` instead of before; a scheduling test whose two recipes'
active windows actually did overlap once the backward-scheduling math
was worked through properly, since every recipe's *last* step always
ends exactly at the shared eat_time). Fixed both by recomputing the
intended schedule by hand before adjusting the fixtures, not by loosening
the assertions - the failures were the tests doing their job.

Also verified the whole suite's syntax (not full execution - `pytest`
isn't installed for that interpreter) parses cleanly under Python 3.11,
the version the Docker image actually runs, given this session's earlier
f-string cross-version bug in the photos feature. Confirmed via direct
inspection that no test touches the real `recipes.db` - row counts
identical before and after a full run.

Docs: README "Running tests" section plus an updated Known-limitations
bullet, ARCHITECTURE.md "Testing" section, MISSING_FEATURES.md marked
resolved (with the scope caveat, not overclaimed as exhaustive coverage).

## 2026-07-13 - Persistent pantry with shelf-life decay

Second half of "let's go with the visible fixes." Specific requirements
given up front, not left to interpretation: any pantry tracking "should
be optional... retain[ed] but intelligently use shelf life information
to discard old knowledge and verify with user when assuming pantry
stock." Both halves of that (auto-discard AND confirm-before-assuming)
are load-bearing design constraints, not just a flat inventory list with
extra steps.

`pantry_shelf_life.py`: deliberately mirrors `flavor_tagging.py`'s
structure exactly rather than inventing a new pattern - a fixed 5-
category taxonomy (`highly_perishable` 4d, `perishable` 10d,
`semi_perishable` 30d, `frozen` 180d, `shelf_stable` 365d), batched LLM
classification against the same `ingredient_embeddings` candidate pool
flavor tagging uses, cached and resumable. Spot-checked a 10-ingredient
batch directly before running the full corpus job: milk->perishable,
canned black beans->shelf_stable, fresh basil->highly_perishable,
frozen peas->frozen, eggs->semi_perishable, etc. - all correct by
inspection. Ran the full ~7,194-ingredient tagging job in the background
while building the rest of the feature (same multi-hour-scale job as the
original flavor-tagging pass earlier this session).

`pantry.py`: `pantry_items` table with no stored status column -
freshness (`fresh` / `needs_confirmation`) is computed from `added_at` +
shelf life on every read, since "now" is the only part of that
calculation that actually changes over time. Thresholds: flagged for
confirmation at 0.8x typical shelf life, discarded outright at 1.5x -
deliberately wide gap, since shelf life is a rough estimate, not a hard
expiration date. `get_confirmed_fresh_names()` (what `/discover`'s
pantry auto-fill uses) excludes `needs_confirmation` items entirely -
verified this is actually enforced, not just intended, by direct testing
with backdated synthetic rows (1 day old -> fresh, 9 days old against a
10-day shelf life -> needs_confirmation, 16 days old -> silently
discarded on the next read, confirmed via `discard_expired_items()`
returning a count of 1 and the item no longer appearing).
`add_or_refresh_item()` resets an existing item's clock instead of
creating a duplicate row - verified two calls for the same ingredient
name produce one row, not two.

Wired into `server.py`: `/pantry` page (needs-confirmation items shown
separately and first, manual add, confirm/remove), a "Find recipes
using my pantry" link that feeds confirmed-fresh names straight into
`/discover?have=`, and - the actual restock mechanism - checking a
shopping-list item ON now calls `pantry.add_or_refresh_item()` (checking
OFF does not remove it from the pantry; unchecking is "changed my mind
about buying it," not "used up what I had"). Verified live: checked off
a shopping-list "eggs" item, confirmed it appeared in `/pantry`
immediately with no separate action. Full `/api/v1/pantry` parity added
to the public API, reusing the same functions.

## 2026-07-13 - Multiple recipe photos (URL + real upload)

User request: "let's go with the visible fixes - photos: any number of
photos per recipe allowed." Asked one clarifying question first since it
was a genuine architecture/security fork, not a style choice: URL-only
(pluralize the existing `image_url` pattern, zero new risk) vs. real
file upload (requires a new, carefully-scoped exception to `server.py`
serving zero static files by design). User answered "both."

`recipe_images.py`: new `recipe_images` table, any number of rows per
recipe, each either an external `url` or a local `filename`. Backfilled
every recipe's existing single `image_url` into a first row on
introduction (39,563 recipes had one - matched 1:1 after backfill).
`recipes.image_url` stays in sync as a denormalized "first image" cache
so every existing thumbnail call site across the app keeps working
unchanged, rather than refactoring every card/search-result render site
to read from the new table.

`uploads.py`: the file-upload half. Filenames are always server-
generated (`uuid4().hex` + a whitelisted extension) and never derived
from client input, so path traversal is structurally impossible, not
merely filtered - verified live by throwing `../../etc/passwd`, URL-
encoded traversal, and a crafted `id/../evil.png` string at
`resolve_upload_path()`, all correctly returned `None`. File type is
validated by real magic-byte content sniffing (not the client-supplied
filename/Content-Type) against JPEG/PNG/GIF/WebP; a plain-text file
renamed to look like an upload was correctly rejected, as was an
oversized (>10MB) upload. `GET /uploads/<filename>` is the one new,
explicitly narrow exception to the app's normal zero-static-files rule,
documented as such in ARCHITECTURE.md with the safety rationale, not
just added quietly.

**Caught by testing under the actual deployed Python version, not just
the dev venv**: the gallery HTML generation used escaped quotes
(`\'li\'`) inside an f-string's `{}` expression, which is fine under
Python 3.14 (this machine's venv, where local testing passed) but is a
hard `SyntaxError` under Python 3.11 (what the Docker image actually
runs - PEP 701 lifted this restriction in 3.12, so the two versions
disagree). The container crash-looped on this after a routine rebuild
that had passed local `python3 -c "import server"` cleanly. Fixed by
moving the JS into a named `removeThumb(this)` function so the inline
HTML attribute needs no quotes/backslashes at all, and re-verified with
`python3.11 -m ast.parse` directly (a local 3.11 install exists) before
rebuilding again, rather than trusting the dev venv's Python version to
represent what ships.

Wired into `server.py`: a photo gallery on the recipe page (add by URL,
add by upload via `FileReader` + base64, remove), plus full
`/api/v1/recipes/<id>/images` parity on the public API reusing the same
`recipe_images.py`/`uploads.py` functions. `docker-compose.yml` now also
volume-mounts `uploads/` (added to `.gitignore`/`.dockerignore`) so
uploaded photos survive a container rebuild the same way `recipes.db`
does.

Verified live against the rebuilt container: added a photo by URL and a
real 1x1 PNG by upload to a real recipe, fetched the uploaded file back
via `/uploads/<filename>` and confirmed it's a valid PNG with the
correct `Content-Type`, confirmed a path-traversal attempt against that
same route 404s, exercised the same flow through the authenticated v1
API, deleted both test images and confirmed `recipes.image_url`
correctly reverted to the recipe's original single pre-existing photo.
All test data and the API keys created during verification cleaned up/
revoked before committing.

## 2026-07-13 - Shopping lists

User request: "yeah let's do shopping lists," following a feature-parity
comparison against Tandoor/Paprika that flagged it as the single
highest-leverage gap - noted at the time that SPEC.md's Phase 3 had
explicitly deferred "integration with grocery shopping lists" pending an
actual user request, which had now arrived. Reversed that deferral in
SPEC.md/README.md/MISSING_FEATURES.md as part of this pass, rather than
just building the feature and leaving the "out of scope" framing stale
elsewhere (per the standing keep-docs-current practice established
earlier today).

Built `shopping_list.py`: `shopping_lists`/`shopping_list_items` tables
(multiple named lists, same pattern as `meal_plans`), manual item
add/remove/check, and `add_recipe_to_list()`/`add_plan_to_list()` which
pull `RecipeDatabase.get_structured_ingredients()` and merge each
ingredient into an existing *unchecked* line sharing the same canonical
`(name, unit)` pair, summing quantities rather than duplicating them -
adding two recipes that both call for "2 cups flour" produces one line,
not two. Deliberately does not attempt cross-unit merging (no
unit-conversion table exists in this project) - "2 cups flour" and
"3 tbsp flour" stay as two honest separate lines. Caught and fixed a
real bug while first testing this locally: the merge query didn't
exclude existing rows with a NULL quantity (e.g. an earlier ingredient
whose parse failed), so combining a new parseable quantity into one of
those raised `TypeError: unsupported operand type(s) for +: 'NoneType'
and 'float'` - fixed by requiring `quantity IS NOT NULL` on the existing
row before attempting to merge.

Wired into `server.py`: `/lists` + `/list/<id>` pages, a full internal
JSON API (`/api/shoppinglist/...`), an "Add all ingredients to a
shopping list" control on the recipe page (dropdown of existing lists +
"new list" via prompt), and a "Generate shopping list from this plan"
button on the meal plan page. Also added full `/api/v1/shopping-lists/*`
parity to the public API, per this project's established full-parity
convention, reusing the same `shopping_list.py` functions - no
duplicated logic between the internal and public surfaces.

Verified live against the real running Docker container (not just the
local venv): created a list, added a real 17-ingredient recipe and then
a second 33-ingredient recipe from a different cuisine to the same list,
confirmed the recipe page's dropdown picked up the newly created list,
confirmed the meal-plan page's "Generate shopping list" button and its
generated page both rendered; checked an item, deleted an item, deleted
the list; ran the equivalent flow again through the authenticated
`/api/v1/shopping-lists/*` endpoints with a real (later-revoked) API
key, including the unauthenticated-request-gets-401 check. Cleaned up
all test lists/plans/keys created during verification before committing.

## 2026-07-13 - Public API (`/api/v1/*`)

User request: "we need a public API for sure," after a feature-parity
comparison against Tandoor/Paprika flagged the lack of one as a real gap.
Two scoping decisions confirmed with the user up front rather than
assumed: API-key auth required (the app has zero authentication anywhere
else), and full parity with the web app rather than a smaller
recipes-only surface.

Built as a separate, versioned namespace (`/api/v1/*`), not a retrofit of
the existing unauthenticated `/api/*` routes those stay exactly as they
are, same-origin implementation details of the web UI's own JS, since
requiring auth on them would have broken every existing page. New
modules: `api_keys.py` (SHA-256-hashed key storage, `create`/`verify`/
`list`/`revoke`) and `manage_api_keys.py` (CLI for the above - no in-app
authenticated session exists anywhere to gate a "create key" endpoint
behind, so key management is deliberately server-side-only). `server.py`
gained a `path.startswith('/api/v1/')` check ahead of each HTTP verb's
existing routing chain, a `_require_api_key()` guard (`Authorization:
Bearer` or `X-API-Key`, checked on every route except `GET
/api/v1/health`), CORS headers plus `OPTIONS` preflight handling, and
~30 endpoints across recipes (CRUD), discovery (keyword/ingredient-based/
LLM fuzzy-intent), categories, preferences, adaptation/invention, meal
plans + backward scheduling, cooking log, notes, and import - each a thin
wrapper around the same functions the internal routes and HTML pages
already call, no duplicated business logic.

Verified live against the running Docker container (not just the local
venv): health check works unauthenticated; a request with no key and one
with a garbage key both get `401`; a real generated key succeeds; a
revoked key correctly gets rejected afterward (created a key, used it
successfully, revoked it, confirmed the next request with that same key
returned `401` without restarting the container - SQLite writes from the
host-side `manage_api_keys.py` CLI were visible to the container
immediately via the shared volume mount); `OPTIONS` preflight returns the
right CORS headers; representative calls succeeded for every endpoint
category, including a meal-plan schedule call that reproduced the same
real 6-conflict example documented in `docs/papers/03-time-planning-engine.md`;
existing web UI routes (`/`, `/recipe/<id>`, `/search`, `/plans`,
`/preferences`) re-confirmed unaffected. Both API keys created during
this development/verification pass were revoked before committing, since
their raw values had already been printed to a terminal and were
therefore no longer safe to treat as secret.

Documented in `docs/API.md` (full endpoint reference, auth flow,
examples) plus updates to README.md (new "Public API" feature-tour
section) and ARCHITECTURE.md (module-map entries for the two new files,
a "Public API" section pointing to docs/API.md as the single source of
truth rather than duplicating the endpoint table in two places, and an
`api_keys` row in the schema table) - prompted by the user flagging that
the API "needs to be clearly documented," which was a fair catch: the
initial build had a docs/API.md but nothing linking to it from the
project's actual documentation entry points.

**Known gap, not addressed in this pass**: README/ARCHITECTURE/PROGRESS
were already stale relative to several features built earlier in this
session (fuzzy-intent "Craving?" search, household preferences, recipe
adaptation, grounded recipe invention, and the four technical-report
write-ups under `docs/papers/`) before this documentation request came
in. Only the public API is caught up here, since that's what was
explicitly asked for - the rest is a real, separate documentation debt.

**Follow-up (2026-07-13, same day):** user asked to also catch up the
flagged gap and to keep documentation current going forward as standing
practice, not just on request. Backfilled the two entries below for the
work that had shipped undocumented, and updated README.md/ARCHITECTURE.md
accordingly (new feature-tour sections, module-map entries, API-route
table rows, and a pass over the whole schema table's row counts, several
of which had also drifted stale from mid-session rebuilds - not just the
two new tables).

## 2026-07-13 (01:25) - Four technical reports documenting the bespoke engines

User asked for the flavor-pairing, recipe-discovery, meal-scheduling, and
generation engines to be written up as scientific-paper-style documents
with real (verified) sources and real figures/tables/diagrams, not
illustrative mockups. Scoped up front via two questions: deliverable
format (markdown in the repo *and* an HTML artifact, not just one) and
whether to cover the three named engines or also a fourth (recipe
adaptation/invention, also built this session) - user chose "all four."

Each of the four reports in `docs/papers/` follows the same method:
identify the actual formula/algorithm in the running code (not a
paraphrase), find and verify real published citations for it via web
search before writing a single sentence that cites one, then compute
every figure/table directly from the live 54,722-recipe corpus or a real
run of the actual application code - nothing illustrative or invented.
This surfaced genuine findings, not just descriptions:

- **No. 1 (flavor pairing)**: `suggest_companions()`'s co-occurrence
  weighting is association-rule *confidence* (Agrawal & Srikant 1994),
  not PMI as its own in-code comment claims - and confidence without a
  support floor is provably biased toward count-1 ingredient pairs,
  reproduced directly against the `onion` co-occurrence data.
- **No. 2 (recipe discovery)**: measured a ~260x latency spread across
  the three discovery paths (5ms FTS5 to ~1.4s LLM-planned intent
  search), and found that 59% of the corpus (the ingredients-only
  CC-BY-NC-4.0 batch) is structurally excluded from two of the three
  paths by the `instructions IS NOT NULL` filter both require.
- **No. 3 (time planning)**: 68.1% of the 113,945 cached instruction
  steps have no extractable duration and fall back to a 5-minute
  default; built a real two-recipe backward schedule (a ramen + an
  Indian potato dish) that showed this inflating a recipe's own
  45-minute `prep_time + cook_time` estimate to a 171-minute scheduled
  timeline, while confirming the conflict-detection algorithm itself
  correctly found 6 genuine active-step overlaps in the same example.
- **No. 4 (adaptation/invention)**: initially hypothesized that rare
  seed ingredients would ground invention poorly (fewer co-occurrence
  partners); tested this against 150 randomly sampled ingredients rather
  than trusting the one anecdotal case that prompted the hypothesis, and
  the data didn't support it - palette size is nearly independent of
  seed rarity. Traced the one real degenerate case (a 2-ingredient
  palette) to its actual cause instead: the seed's only occurrence was in
  a recipe whose ingredient list had only 2 parsed rows, a parsing
  under-coverage issue, not a rarity issue.

Published as one combined HTML artifact (self-contained, figures
embedded as base64 data URIs) via the artifact-design skill, and
committed the markdown sources + PNG figures + a matching self-contained
HTML copy to `docs/papers/` in the repo.

## 2026-07-13 (00:20-00:41) - Recipe discovery roadmap: ingredient/flavor discovery, Craving search, preferences, adaptation, invention

User asked a broad, open-ended question about "recipe discovery":
finding existing recipes via natural-language intent (not just keyword
search) and creating new ones that respect the household's implied/
stated rules, potentially by adapting an existing recipe rather than
inventing from nothing - and explicitly left the approach open ("what do
you think about this... you figure this part out"). Verified first that
no preferences/settings system existed anywhere in the codebase, then
proposed a 5-step roadmap; user approved proceeding through all 5 in
order ("ok go with what you've suggested in order").

1. **`/discover?have=`** - `RecipeDatabase.find_recipes_by_ingredients()`,
   a structured query against `recipe_ingredients` ranked by match count
   then `completeness_score`. No model involved by design - this is data
   already in the corpus, not a fuzzy problem.
2. **Companion suggestions surfaced outside meal planning** - added a
   "Pairs Well With" section to the recipe page itself and a standalone
   `/pairings?ingredient=` lookup, both reusing `meal_planner.py`
   functions that previously only ran inside the meal-plan flow.
3. **Bug found and fixed while building step 4**: `flavor_queries.py`'s
   `_normed_ingredients()` was still keying off raw `recipes.ingredients`
   text instead of canonical `recipe_ingredients.name` - the same bug
   class already caught and fixed twice earlier this session in
   `rebuild_ingredient_pairs()` and `suggest_companions()`. Verified live
   before the fix: `get_recipe_flavor_profile(1226)` (a recipe with
   quantity-prefixed raw ingredient text from this session's earlier
   backfills) returned `flavor_counts: {}` - completely empty despite
   every one of its ingredients actually being tagged. Fixed by rekeying
   `get_recipe_flavor_profile()`/`get_cuisine_flavor_profile()` through a
   shared `_canonical_ingredients_for_recipe()` helper; re-verified the
   same recipe returned correct, non-empty flavor counts afterward, both
   directly and through the live recipe page's "Pairs Well With" section.
4. **`/craving?q=`** - `query_planner.py` (new) sends free text to a
   local Qwen3-8B instance to extract flavor categories/cuisine/a time
   constraint against a fixed vocabulary; `recipe_flavor_index.py` (new)
   precomputes a `recipe_flavors(recipe_id, flavor, weight)` table so
   ranking against the extracted flavors is one indexed SQL query instead
   of re-deriving each candidate recipe's profile in Python. Falls back
   to keyword search on the LLM's own extracted keywords if nothing is
   extracted or nothing matches - verified live with 5 hand-written
   queries spanning a clear-flavor case, a clear-cuisine-and-time case,
   and a deliberately vague one ("use up leftover rice") that correctly
   triggered the fallback path instead of forcing a spurious flavor
   guess.
5. **`preferences.py`/`/preferences`** (new) - one singleton household
   record (dietary restrictions, disliked ingredients, free-text notes),
   since no multi-user/auth concept exists anywhere in the app. Wired a
   "Heads up: contains X" conflict note into the recipe page.
6. **`recipe_adaptation.py`** (new) - `suggest_substitutions()`
   (embedding-nearest alternatives for disliked ingredients) needed a
   plural-aware stemmer beyond the existing substring-dedup in
   `top_embedding_similar_ingredients()`, since "blueberries" and
   "blueberry" don't share a substring relationship and were showing up
   as "substitutes" for each other before the fix.
   `adapt_recipe_to_preferences()` sends the full recipe + preferences to
   the LLM for the smallest set of changes that satisfy them - verified
   live: recipe 1226 with `disliked_ingredients=["blueberries"]` and
   `notes="make it dairy-free if possible"` correctly substituted
   blackberries and flagged the yogurt as dairy-free in one pass.
7. **`recipe_invention.py`/`/invent`** (new) - `build_ingredient_palette()`
   expands seed ingredients via real `ingredient_pairs` co-occurrence
   before generating, explicitly contrasted with `easter_egg.py`'s
   ungrounded comedic riffs (see Technical Report No. 4 above for the
   full contrast and a corrected hypothesis about grounding quality).
   Verified live end-to-end: invented "Spiced Chickpea and Celery Stew"
   from `onion`+`chickpeas` seeds, saved it through the real API as a new
   recipe, confirmed it rendered correctly, then deleted the test recipe.

Every step in this list was verified against the actual running Docker
container (rebuild + real HTTP requests), not just local unit checks -
consistent with this project's standing practice, and the same practice
is what caught item 3's bug rather than assuming a clean-looking rebuild
meant the feature worked.

## 2026-07-12 (operator) - Lifted the no-numpy rule

User decision, not a code change: the "no numpy" constraint (see
ARCHITECTURE.md's design principles, and the Phase 3 embedding-pairing
entry below where it was first tested and upheld) is no longer project
policy. It came up while investigating `ingredient-parser-nlp` (a
sequence-labeling ingredient parser, MIT licensed, meaningfully more
accurate than `recipe_scaling.py`'s hand-rolled regex heuristic on
compound quantities and size-vs-quantity disambiguation - see e.g.
"2 tablespoons plus 1/4 cup sugar, divided" and "1/4-inch piece of fresh
ginger") as a possible upgrade; it depends on `numpy`, which the stated
design principle excluded on principle rather than on demonstrated need.

Original rationale (still recorded accurately below, in the Phase 3
entry): at ~8,500 embedding vectors, pure-Python cosine similarity was
fast enough, so numpy wasn't *necessary* at the time. That's a real but
narrow finding - it was never a broader case against numpy generally, and
the user has now explicitly opted to allow it. ARCHITECTURE.md's design
principles section is updated accordingly. This entry doesn't itself add
numpy anywhere or change any running code - `ingredient-parser-nlp`
adoption is still an open decision, not yet made.

Last item from the revised order (Beautiful -> Accessible -> Elegant #1 ->
security headers). Added `Content-Security-Policy`, `X-Content-Type-Options:
nosniff`, and `X-Frame-Options: DENY` via a single `RecipeHandler.end_headers()`
override rather than touching every individual response call site. The CSP
allows `'unsafe-inline'` for script/style deliberately - every page's CSS/JS is
genuinely inline (no separate static files, no nonce/hash infra), so this isn't a
rewrite of that architecture, just closing off other injection vectors (loading
an external script, being framed, MIME-sniffing). This is defense in depth on top
of this session's earlier `escape_html()` sweep, not a replacement for it.

Real auth/CSRF was explicitly excluded, per the earlier grading pass - it
contradicts Sous's own stated "no accounts" design (README/SPEC.md), so chasing
that grade further isn't worth fighting the product's actual scope.

**Verified the headers don't break anything, not just that they exist**: curled
both an HTML route and a JSON API route to confirm all three headers are present
on both (the override is shared by `end_headers()`, so this wasn't a given).
Then loaded the home page in real Playwright-driven Chrome and confirmed zero CSP
violation errors in the console - inline scripts, inline styles, and external
recipe images (many different source hosts) all still load under the new policy.
Full regression check (pagination, FTS search, structured-model scaling) re-run
clean.

**This closes out the full "most beautiful/elegant/useful app" work**: Beautiful
went from a documented C+ to photo-forward cards + a home hero; Accessible
picked up real form labels and `aria-live` regions; Elegant lost its
reload-per-action pattern for notes/cook-log/plan actions; security headers
landed as a cheap final step. Deferred, on purpose, not forgotten: real auth
(conflicts with product scope), the automated test suite (task remains queued),
and any architecture refactor/threading work.

## 2026-07-11 (operator) - Elegant #1: kill gratuitous full-page reloads

Continuing the "most beautiful/elegant/useful app" self-assessment work (see the
prior entry for Beautiful/Accessible). This is the Elegant item: notes, cook-log,
and plan add/remove all used to `window.location.reload()` on success - the single
biggest named complaint in the original grading pass.

**Notes/cook-log** (`serve_recipe`): the empty-state placeholders (`"No notes
yet."`, `"Not logged as cooked yet."`) got stable ids (`notes-empty`,
`cook-log-empty`) so the DOM-patching logic can target them precisely instead of
inferring "is this still empty" from fragile DOM shape. Add-note now builds a real
`<li>` via `createElement`/`textContent` (matching the codebase's existing
no-`innerHTML`-for-user-data convention) and appends it; the delete-note handler
was pulled into a named `deleteNoteHandler` function so both server-rendered notes
and newly-added ones share the same logic, and re-adds the empty-state placeholder
if the list becomes empty. Mark-cooked prepends today's date
(`new Date().toISOString().slice(0,10)`, mirroring `cooking_log.log_cooked()`'s own
default - `cooking_log.py:65`).

**Plan add/remove** (`serve_plan_detail`) was the hard case flagged during
planning: adding/removing a recipe also recomputes "Suggested Companions" (PMI +
embedding scoring) and "Cooking Timeline" (backward scheduling with conflict
detection) - real algorithms that can't be reimplemented client-side. Extracted the
three dynamic sections into a new shared `_render_plan_fragments(plan_id)` method,
reused by both the existing full-page route and a new `GET
/api/plan/<id>/fragment` JSON endpoint (`serve_plan_fragment`) that returns the
same three sections as already-`escape_html()`-safe HTML strings. `addRecipe()`/
`removeRecipe()` now POST/DELETE as before, then call a new `refreshPlanFragments()`
that fetches the fragment endpoint and sets the three containers' `innerHTML` from
it - safe specifically because that HTML was escaped server-side before being
sent, not because `innerHTML` is safe in general.

**Verified via real Playwright-driven interaction, not curl or fixed timeouts**:
clicked "Add Note", confirmed a real `<li>` appears with today's date and a working
delete link, no page navigation occurred (`framenavigated` event count unchanged).
Clicked "I cooked this today", confirmed the entry prepends correctly. Clicked
"Add"/"Remove from plan" on a real plan - confirmed the recipe card appears/
disappears, confirmed "Suggested Companions" populated with real scored
suggestions (not a stub), confirmed zero navigations occurred either way.

**One real methodology bug caught and fixed while testing, not in the app itself**:
an early test run using a fixed `wait_for_timeout(700)` after clicking "Add"
reported the DOM never updated - looked like the app was broken. Investigating
found the app was correct (verified by awaiting the same fetch chain directly via
`page.evaluate` - worked immediately) and the *test* was racing a slightly slower
real request. Switched to Playwright's proper `wait_for_selector` awaiting instead
of a fixed sleep, which is what should have been used from the start - noted here
so the same false alarm doesn't get chased again.

`recipe_notes`/`cook_log` test rows written to the real `recipes.db` (recipe 1227)
during manual testing before the Playwright test suite was in place - confirmed
cleaned up via direct SQL, not just assumed. All Playwright-created scratch meal
plans were deleted via the real API after each test. Full regression check
(pagination, FTS search, structured-model scaling) re-run clean.

## 2026-07-11 (operator) - "Most beautiful/elegant/useful app" self-assessment: Beautiful + Accessible fixes

User asked how Sous would fare entered into "most beautiful/elegant/useful app"
categories. Graded each honestly (Useful A-, Elegant B, Beautiful C+), ranked the
concrete levers for each by how far they alone would move the grade, and got
approval to build the top-ranked items in order: photo-forward recipe cards +
a home-page hero (Beautiful), then killing gratuitous full-page reloads (Elegant).
Used `EnterPlanMode` given the scope - plan saved to
`~/.claude/plans/steady-wobbling-pearl.md`.

Mid-implementation, the user asked about additional categories. Graded three more
the same way (Accessible: B- baseline; Trustworthy/Secure: C+ baseline, capped at
B+ since real auth/CSRF would contradict Sous's own "no accounts" design in
README/SPEC.md - excluded on principle, not effort; Innovative/Architecture: B+
baseline, mostly closed by the already-queued test suite). Re-evaluated the
remaining work order on pure forward-looking ROI, explicitly not sunk-cost
reasoning (no code existed yet for the reload-removal work, so nothing was actually
sunk) - Accessible's fixes are cheaper, safer, and a genuine *prerequisite* for the
reload-removal work (removing reload-per-action without `aria-live` regions is a
regression for screen-reader users, not neutral). Revised order: **Accessible ->
Elegant #1 -> security headers**, deferring auth/tests/architecture refactor.

**Photo-forward recipe cards** (Beautiful #1): `.recipe-card` redesigned from a
64x64-thumbnail flex row to a vertical card with the image filling the top
(`aspect-ratio: 4/3`, `object-fit: cover`). Applied consistently across all four
places a recipe renders as a card - home (JS-built), search, category, and plan
detail (the last two had no image at all before). New shared
`recipe_thumb_html()` helper (server.py) for the three server-rendered sites; the
home page's JS mirrors the same fallback logic separately since it's client-built.
Added a designed placeholder (a simple two-circle "plate" glyph on a gradient,
inline SVG, no external asset) for the ~28% of recipes with no image - a flat empty
box read as broken once the layout made the image this prominent. Missing-URL case
uses the placeholder directly (known at render time); a URL that 404s at runtime
still just falls back to `this.remove()`, matching the codebase's existing
convention, rather than adding JS complexity for a rare edge case.

**Home-page hero** (Beautiful #2): new `RecipeDatabase.get_random_recipe_with_image()`
(recipe_model.py) backs a hero at the top of the home page - real photo (up to
380px), a mono eyebrow ("Sous · today's pick") matching the existing ticket-motif
style, serif title, truncated description, CTA - replacing the plain "Sous Recipe
Manager" h1. Falls back to that plain h1 if no recipe has an image (verified
against an empty scratch DB, not just assumed).

**Accessible fixes**: audited every `<input>`/`<textarea>` in `server.py` for
placeholder-only labeling (a WCAG failure - placeholder text is not a label).
Found and fixed 4: the home search box, the note-text textarea, and the two file
inputs on the import page - added a new `.sr-only` CSS utility for cases where a
visible label would be redundant next to already-clear context. Added
`aria-live="polite"` to every container the upcoming reload-removal work will
patch without a full navigation: `#notes-list`, `#cook-log-list`, and (new,
previously unwrapped) `#suggestions`/`#schedule` on the plan-detail page, alongside
the existing `#recipe-list` there.

**Verified, not assumed**: installed Playwright this session (`pip install
playwright`, pointed at the existing system `google-chrome` binary rather than
downloading a separate Chromium - confirmed working) specifically so real browser
interactions/console errors/accessibility-tree state can be checked directly,
instead of curl-only verification - the exact gap that let the pagination
regression ship silently earlier this session. Screenshots (desktop/mobile) across
home/search/category confirm the card redesign and placeholder both look
intentional; zero console errors on load. Confirmed via the accessibility tree
that the search input's computed accessible name is now "Search recipes" (not
just visually labeled). Confirmed `aria-live="polite"` present on all five target
containers via direct HTML inspection, not a regex that could silently miss
attributes. Full regression check (pagination, FTS search, structured-model
scaling) re-run clean after all changes.

## 2026-07-11 (operator) - UI accessibility/layout audit and fixes

User asked whether the UI followed best practices around layout etc.
Rather than answer from general knowledge, audited the live app
(headless-Chrome screenshots at desktop/mobile widths across every page)
and the underlying markup/CSS in `server.py`, then fixed what was found,
verified live.

**Caught a real, already-live bug in the process**: the home page's
recipe list had been completely broken since the pagination commit
(`d4b65b0`) - a stray quote in the generated `fetch()` call
(`+ 1'` instead of `+ 1`) was a JS syntax error that silently killed the
entire inline `<script>` block, so `window.onload` never ran and no
recipe cards ever rendered below the search box. The pagination
controls (server-rendered) still worked, which is why it wasn't obvious
from the markup alone - only actually loading the page in a browser
caught it. Fixed and committed separately (`1f3cfd6`) before the audit
continued, since it was a severity-1 regression.

**Accessibility/layout fixes** (all in `server.py`, verified live against
a fresh server instance):
- **No visible keyboard focus indicator on links/buttons** - only form
  inputs had one. Added `:focus-visible` outlines to `a`/`button`/`.btn`/
  `.print-button`.
- **No landmark elements or skip link** - added `<main id="main-content">`
  wrapping every route's content (verified open/close tag balance across
  all 10 routes via a script, not by eye), and a skip-to-content link in
  `get_nav_html()` (so it's on every page that has nav) that's visually
  hidden until focused.
- **Missing `lang` attribute** - `<html>` -> `<html lang="en">` on all 10
  routes' templates.
- **Muted text contrast was borderline**: `#7a6f60` on the parchment
  background measured 4.37:1, just under WCAG AA's 4.5:1 for normal
  text (used heavily - recipe metadata, the ticket line, timestamps).
  Computed a replacement (`#6d6353`, same warm-brown family) that hits
  5.24:1, comfortably over AA, with a small script rather than eyeballing
  it.
- **No confirmation on destructive actions** - added `confirm()` before
  delete-note and remove-recipe-from-plan. (Delete-recipe and delete-plan
  have real API routes but no UI button anywhere to reach them at all -
  a separate, pre-existing gap, not fixed here since it's a missing
  feature, not an accessibility/confirmation issue.)
- **Inconsistent `alt` text on recipe images** - the print view already
  used `alt="{title}"` correctly; the home page's JS-built cards, the
  search-results cards, and the recipe detail hero image all used
  `alt=""` (decorative) despite being actual photos of the dish. Made
  all four consistent with the print view.
- **Desktop layout wasted the full width** - fixed `max-width: 720px` at
  every viewport size, so a 1440px window looked identical to an 800px
  one with large empty margins. Added a `body.list-page` opt-in (applied
  to home/search/category/plans-list, *not* recipe-detail/forms, since a
  wide single column of prose/instructions is harder to read than a
  narrow one) that widens to 1100px and turns `#recipe-list`/`#plan-list`
  into a responsive `auto-fill` grid above 860px - verified via
  screenshot that recipe-detail stays narrow while the home page goes to
  a real 3-column grid at 1440px.
- **Found and fixed one more bug while in the CSS**: the pagination
  Prev/Next disabled state renders as `<span class="disabled">`, but the
  CSS only targeted `a.disabled` - dead selector, disabled state was
  never actually styled as disabled. Fixed to `.pagination .disabled`.

Full regression check after all changes: pagination, FTS search, and
structured-model scaling all re-verified live and still correct.

## 2026-07-10 (operator) - Structured scaling/cook-time data model, full backfill

User asked for this directly (while reviewing MISSING_FEATURES.md's
"Documented tradeoffs" section) as something to actually fix, not just
document - along with confirming the missing-image tradeoff is fine
as-is (see the new feature idea logged below) and flagging the CC-BY-NC
licensing note as something to just keep in mind. Chose (via explicit
questions asked before starting): tackle this before the test suite, and
backfill all 54,722 existing recipes immediately rather than only
structuring new/edited ones going forward.

Integrate-with-existing (touches `recipe_model.py`'s core save/delete
paths, `meal_planner.py`'s init, and `server.py`'s scaling route), built
directly rather than dispatched, per this project's established
heuristic. Reused the *existing* heuristic parsing logic throughout
(`recipe_scaling.parse_quantity()`'s fraction/decimal parsing,
`meal_planner`'s `extract_step_duration()`/`classify_step_type()`) rather
than inventing new extraction - there's no ground-truth structured
ingredient/timing data in the source datasets to parse from instead, so
the actual change is *where the parse result lives* (computed once,
persisted, queryable) not a smarter parser.

**`recipe_ingredients`** (new table, `recipe_model.py`): one row per
ingredient line (`recipe_id, position, raw_text, quantity, unit, name`).
Added `recipe_scaling.parse_ingredient()` (extends the existing
`parse_quantity()` with a ~25-unit alias table - cup/tbsp/tsp/oz/lb/g/
kg/ml/l/pinch/dash/clove/can/package/slice/stick/qt/pt/gal/bunch/sprig/
head/jar/stalk - to split the remainder into unit + name) plus
`scale_structured_ingredient()`/`scale_recipe_to_servings_structured()`
for scaling and re-rendering from structured fields, including
plural-aware unit formatting (`2 cup` -> `4 cups`, but abbreviation units
like `tbsp`/`oz`/`g` stay unpluralized, matching normal recipe-writing
convention). Self-initializing like the FTS index from the prior entry:
`RecipeDatabase.init_database()` backfills on first run if the table is
empty and `recipes` isn't; `save_recipe()`/`delete_recipe()` keep it in
sync going forward (`_sync_structured_ingredients()`, same
delete-then-reinsert pattern as `_sync_fts()`). `server.py`'s recipe-page
scaling form now calls `db.get_structured_ingredients()` +
`scale_recipe_to_servings_structured()` instead of re-parsing
`recipe.ingredients` raw text on every request.

**`recipe_steps`** (existing table, `meal_planner.py`): previously only
populated lazily by `get_recipe_steps()` on a recipe's first
backward-schedule request - left at 26 rows across 3 recipes from
whatever ad-hoc testing had touched it historically. Added an eager
backfill in `MealPlanDatabase.init_database()` covering every recipe
with instructions. **Caught a real bug while verifying this**: the first
version guarded the backfill with "skip if the table isn't empty",
which wrongly treated those 26 leftover rows as "already fully
backfilled" and silently skipped all 15,269 recipes. Fixed to check
per-recipe coverage (`id NOT IN (SELECT DISTINCT recipe_id FROM
recipe_steps)`) instead of table-level emptiness - caught by actually
inspecting the row/distinct-recipe counts after the first backfill run
rather than trusting a plausible-looking result, per this project's
standing distrust of unverified claims (including my own).
`get_recipe_steps()` itself is unchanged and still self-heals if a
recipe's instructions are edited after this backfill.

**Verified, not just run**: timed the full backfill against a scratch
copy of the real `recipes.db` (~5s total for both tables together - no
separate migration script needed, matching the FTS index's
self-initializing pattern from the prior entry). Confirmed counts:
495,503 `recipe_ingredients` rows across 54,717 recipes (5 recipes have
zero ingredients), 73,205 `recipe_steps` rows across exactly the 15,269
recipes that have instructions. Spot-checked parse quality (e.g. `"2
teaspoons kosher salt"` -> `quantity=2.0, unit='tsp', name='kosher
salt'`; `"3 large eggs"` -> `quantity=3.0, unit=None, name='large eggs'`
- no unit false-positive on "large"; `"2 (8 oz.) cans tomatoes"` ->
scales the leading 2 only, matching the documented scope limit). Verified
scaling end-to-end through the real running server: recipe 2450 (real
quantity-bearing ingredients, unlike many recipes in this collection
whose ingredient lines are bare names with no quantity at all) scaled
from 1 to 4 servings correctly (`"2 teaspoons kosher salt"` ->
`"8 tsp kosher salt"`, `"¾ teaspoon...pepper"` -> `"3 tsp...pepper"`).
Verified cascade-delete (added `recipe_ingredients` to the prior entry's
cascade list) against a scratch recipe. Real `recipes.db` confirmed at
54,722 recipes / 495,503 `recipe_ingredients` / 73,205 `recipe_steps`
after the real backfill ran (triggered by starting the real server, not
a separate script).

**Not committed this entry**: `recipes.db` itself - same reasoning as
the FTS entry (the backfill is self-initializing on any checkout, so
shipping the data isn't necessary, and doing so would conflate a
verified change with the still-unexplained row-count drift). Updated
README/ARCHITECTURE's "heuristic, not structured" language to reflect
that the *parse* is still heuristic but its *output* is now real
persisted structured data - see MISSING_FEATURES.md's "Resolved" section
for the full before/after.

**Also from this check-in**: confirmed the "no image for ~13,495
recipes" tradeoff needs no fix (not every recipe needs one), but it
produced a real feature idea worth keeping - supporting *multiple*
images per recipe (e.g. one per cooking step), not just today's single
`image_url`. Not scheduled, logged in MISSING_FEATURES.md so it isn't
lost. CC-BY-NC-4.0 licensing needs no action, just ongoing awareness.

**Queued next** (not started): an automated test suite - there still
isn't one, and this session added a meaningful amount of new
surface area (FTS, pagination, cascade delete, two new structured-data
tables) with no regression net beyond manual live verification.

## 2026-07-10 (operator) - Fixed all four MISSING_FEATURES.md "Needs attention" items

User asked directly for these to be fixed, not just documented. All
integrate-with-existing (touch `server.py` broadly, or `recipe_model.py`'s
core read/write paths), built directly rather than dispatched, per this
project's established heuristic for that category. Full sweep, verified
live against a running server (and a scratch DB copy for the destructive
cascade-delete test) rather than trusted by inspection.

**XSS**: audited every HTML-rendering function in `server.py` (previously
only `serve_search`'s recipe cards called `escape_html()`) and wrapped
every recipe/user-derived interpolation - titles, descriptions,
ingredients, instructions, notes, cuisine, category names, plan names,
target eat times, schedule/conflict text, flavor tags, the reflected
`?q=` search query, easter-egg riff text and its error message. Verified
by creating a real recipe via the API with `<script>alert(1)</script>`
and `<img src=x onerror=...>` in title/description/ingredients/
instructions and confirming the recipe page and search page both render
them as literal escaped text, not markup; also confirmed the reflected
`?q=<script>...</script>` search case. Deleted the test recipe after.

**Cascade delete**: `RecipeDatabase.delete_recipe()` now deletes matching
rows from `recipe_categories`, `recipe_notes`, `cook_log`,
`recipe_steps`, `meal_plan_items`, and `recipes_fts` in the same
transaction before deleting the recipe row itself (tables that don't
exist yet - e.g. a fresh DB where a feature was never used - are
detected via `sqlite_master` and skipped rather than erroring). Verified
against a throwaway copy of `recipes.db` in `/tmp`: seeded one row in
every cascade table for a scratch recipe, called `delete_recipe()`,
confirmed all six tables returned to 0 rows for that id via direct SQL.

**Pagination**: `get_all_recipes()` and `search_recipes()` now take
`limit`/`offset` (default page size 24, a new `PAGE_SIZE` constant), plus
new `count_recipes()`/`count_search_results()` for totals. Home and
search pages both render a `get_pagination_html()` Prev/Next control with
a running total, wired through `?page=` (parsed/clamped by a shared
`parse_page()` helper). `/api/recipes` also takes `?page=`. Verified
live: `/api/recipes?page=1` vs `?page=2` return disjoint id ranges,
`/?page=1` shows Prev disabled, `/search?q=chicken&page=2` preserves `q`
and shows distinct results from page 1, and the total-count math is
correct (confirmed independently: `recipes.db` actually has **54,722**
rows right now, not the 54,167 cited everywhere in older docs - see the
open question below).

**Full-text search**: replaced the `LIKE '%query%'` scan with a SQLite
FTS5 virtual table (`recipes_fts`, `tokenize='porter unicode61'`) over
title/description/ingredients, ranked by `bm25()`. Not a contentless FTS
table - the indexed text is duplicated on disk rather than referencing
`recipes`, a deliberate simplicity tradeoff at this data size. User input
goes through a new `_build_fts_match()` (tokenizes on `\w+`, drops
anything that isn't a word character, turns each token into a `"tok"*`
prefix match, ANDed by FTS5's default query syntax) rather than being
passed to `MATCH` raw, so FTS5 query-syntax characters in a search
(`" * ( ) -` etc.) can't produce a syntax error - both `search_recipes()`
and `count_search_results()` also catch `sqlite3.OperationalError` and
fall back to the old LIKE scan just in case. The index is
self-initializing: `init_database()` creates the virtual table and, if
it's empty while `recipes` isn't, backfills it in one `INSERT ... SELECT`
- took under a second for all 54,722 rows, so this needed no separate
migration step or script. `save_recipe()`/`delete_recipe()` keep it in
sync (delete-then-reinsert on save, since FTS5 has no column-level
UPDATE). Verified live: `chicken curry` ranks "Malaysian Chicken Curry"
first (title match) ahead of pure-ingredient matches; `chick` prefix-
matches "Chicken"/"Chickpea"; a deliberately hostile query
(`" * AND OR NOT (` etc.) returns 200 with results, not a 500; created a
recipe, confirmed it's immediately searchable, deleted it, confirmed it's
gone from search and `recipes_fts`'s row count tracks `recipes`' exactly
(54,722 both, before and after the create+delete).

**Open question, not resolved this session**: `recipes.db` has 54,722
rows, not the 54,167 figure repeated throughout README/ARCHITECTURE/this
file. This is independent confirmation of the uncommitted `recipes.db`
diff flagged earlier the same day (389,152,768 bytes vs. the last
committed 387,768,320) - still not investigated. `recipes.db` was *not*
committed alongside this session's code changes for the same reason as
before: the new `recipes_fts` index is self-initializing (any checkout
rebuilds it on first run), so there's no need to ship it, and doing so
would conflate a verified intentional change with the still-unexplained
drift.

**User also flagged while this work was in progress** (see the
"Documented tradeoffs" section of `MISSING_FEATURES.md`, updated to
match): scaling/cook-time heuristics should become a real structured data
model, and an automated test suite is needed - both queued as separate
follow-up work, not done in this entry. The "recipe has no image" gap was
confirmed fine as-is, but prompted a new feature idea (multiple images
per recipe, e.g. per-step) logged for later. The CC-BY-NC-4.0 licensing
tradeoff needs no action, just awareness going forward.

## 2026-07-10 (operator) - Missing-features doc + configurable port

Two small, independent operator asks, not PLAN.md items.

**`PORT` env var**: `run_server()` was already parameterized, but
`if __name__ == "__main__"` hardcoded `port=8000`. Changed to
`int(os.environ.get("PORT", 8000))` (one line, `server.py`). Verified live:
`PORT=8131 python3 server.py` actually bound 8131 and served a real 200 on
`/` (curl). Default behavior unchanged for anyone not setting `PORT`.
Documented in README's Quickstart. This machine specifically has an
unrelated `uvicorn` process permanently on 8000, which is why this came
up - see prior entries.

**`MISSING_FEATURES.md`** (new file, linked from README): consolidates
what was previously scattered across README's "Known limitations",
PLAN.md's one deferred checkbox, and old PROGRESS.md "What's Next" notes
- and adds real gaps found by re-reading the current code rather than
trusting those old notes, most notably: **`serve_search`'s recipe cards
are the only place in `server.py` that calls `escape_html()` on
recipe-derived text** (added incidentally while that function was being
touched for something else - the comment there references an
out-of-repo "task #8" that never got finished). Every other page
(recipe detail, category, meal plan, print, easter-egg) interpolates
`recipe.title`/`description`/etc. into HTML unescaped. Since recipe
content comes from bulk imports and the quick-add form, this is a real
stored-XSS gap, not a cosmetic one - flagged prominently rather than
fixed here, since fixing it means auditing every interpolation site
across the file, which is its own task. Also confirmed still-real and
previously undocumented: no pagination anywhere (`get_all_recipes()`
hardcodes `LIMIT 100`, `search_recipes()` hardcodes `LIMIT 50`, no
offset/next-page control - browsing past the first 100 recipes isn't
currently possible outside search), and search is plain `LIKE
'%query%'` with no ranking or FTS index.

## 2026-07-10 (operator) - "Recipe Box" visual redesign

Not a PLAN.md item - a polish/aesthetics pass on top of the completed
feature set, requested directly by the user. Integrate-with-existing
(rewrites the shared `get_base_style()` CSS and the recipe-detail meta
markup, both existing code touched by every page), built directly rather
than dispatched, consistent with this project's established heuristic.

Design direction ("Recipe Box", chosen from 3 proposed options): warm
parchment palette (`#f6f1e7` bg, sage green primary, paprika-rust accent,
muted turmeric highlight) with a cookbook-style serif for headings/recipe
titles (`--font-display`, system stack only - no external font load, per
this project's no-external-services principle) over the existing sans
body font. Dark mode (`prefers-color-scheme`) carries the same palette
family (charcoal/chalk-white/sage/rust) rather than being restyled
separately. Signature element: recipe detail page's meta line (cuisine/
servings/prep/cook time) now renders as a `.recipe-ticket` - a dashed-
divider, uppercase monospace strip evoking a kitchen order chit, replacing
the old plain `cuisine | N servings | Prep: X min` text line.

Scope: `get_base_style()` (design tokens + component CSS) and the
recipe-detail meta markup only. Recipe cards, nav, buttons, forms,
tags all picked up the new palette automatically since they already
consumed CSS custom properties rather than hardcoded colors.

**Verified visually, not just "compiles"**: ran the real server
(`python3 server.py`, port 8130 - 8000 occupied by an unrelated process
on this machine, same as prior entries), captured real headless-Chrome
screenshots (`google-chrome --headless --screenshot`, both
`preferredColorScheme=1` light and default/dark) of the home page and a
real recipe page (id 1227, Biryani - has an image and multiple ingredients)
at desktop (1280px) and mobile (390px) widths. Confirmed: serif headings
render correctly in both color schemes, the `.recipe-ticket` line renders
as `6 SERV` (this recipe has no prep/cook time or difficulty data - ticket
correctly omits empty fields rather than showing "0 min"), category tag
pill picks up the new tag colors, dark mode holds together as one palette
rather than two different designs. No DB reads/writes involved - visual-
only change.

## 2026-07-09 (operator check-in) - Phase 12 complete + Phase 13 complete: easter eggs

**Phase 12 finished**: surfaced flavor data in `suggest_companions()` -
each suggested companion now carries its top-3 flavor tags plus the seed
recipe's own profile (via `flavor_queries.get_recipe_flavor_profile`),
rendered on the meal plan page. Integrate-with-existing (touches an
existing function and route), built directly rather than dispatched.
Verified live: created a real scratch plan via the actual API, added
recipe 1226, confirmed the rendered HTML shows `(leans sweet, floral,
sour)` next to the seed and real per-candidate flavor tags next to each
suggestion; all other routes (home/search/plans/print) still 200; scratch
plan deleted, `recipes.db` back to 54,167/0 meal_plans. Ranking itself is
unchanged - this is display-only surfacing, not yet using
`flavor_pair_stats` to influence suggestion ranking (that's the
recipe-creation-assistance idea in SPEC.md, left for later). Phase 12's
only remaining checklist item is the explicitly-deferred external
flavor-dataset research (deferred by design per SPEC.md, not a gap).

**Phase 13 (Delight) built and complete**: `easter_egg.py`
(`generate_easter_egg_recipe(recipe, timeout=90)`) shells out to the
`claude` CLI (`--tools ""` to disable all tool use - pure text generation,
`--max-budget-usd 0.20` per call as a cost guard) with a prompt asking for
a short comedic riff on a real recipe already in the collection, keeping
the dish's identity recognizable. New route `GET
/recipe/<id>/easter-egg` (`serve_easter_egg`) renders the result; a
"Comedic riff" button was added next to the print button on the recipe
view page. Deliberately not persisted - regenerated fresh each visit.

Verified for real, not simulated: standalone test against recipe 1226
took 7.8s and produced a genuinely funny, on-topic riff ("Non-GMO
Emotional Support Popsicle" / "hostage negotiation" ice-cream-churning
bit). Live-tested through an actual running server instance (had to
restart it on a fresh port after discovering an earlier stale test server
process - backgrounded jobs don't survive across separate tool-call shell
invocations, a real gotcha worth remembering for any future live-server
testing in this project): the button renders on `/recipe/1226`, the real
route returns 200 with fresh generated text, a bad recipe id correctly
404s, and all other core routes (home/search/plans/print) remain
unaffected. `recipes.db` correctly untouched throughout (54,167 rows,
read-only feature).

**All of Phases 9-13 are now complete.** Remaining open items across the
whole backlog: the two explicitly-deferred/scoped-out items noted in their
respective phases (external flavor-dataset research; recipe scaling's
missing UI entry point, noted in an earlier Phase 11 entry) - neither
blocks calling the originally-requested feature set done.

## 2026-07-09 (operator check-in) - Phase 12: flavor-pairing stats verified

The 14:30 cron cycle's dispatch (`flavor_pairing.py`, commit `2fc7839`,
author `Sous Project <sous@example.com>` - the dispatch container's own git
identity) succeeded outright this time - first dispatch today that didn't
die silently. Independently verified before trusting it, not just because
it ran:

- `rebuild_flavor_pair_stats()` had already been run for real against
  production `recipes.db` (`flavor_pair_stats` table present, 153 rows =
  the full theoretical max of C(17,2)+17 unordered pairs including
  self-pairs across the 17-name taxonomy).
- **Manually recomputed one pair from raw data independently of the
  module**: iterated `ingredient_pairs` (1,016,901 rows) x
  `ingredient_flavors` (18,887 rows) myself in a separate script, summing
  `pair_count` for every ingredient-pair whose flavor tags include both
  sweet and sour. Got `103,744`, which exactly matches
  `get_flavor_pair_count('sour','sweet')`'s stored value - confirms the
  rollup math itself, not just that the table has some plausible-looking
  numbers in it.
- Confirmed order-independence (`get_flavor_pair_count('sweet','sour')` ==
  `get_flavor_pair_count('sour','sweet')`).
- `get_common_flavor_pairs()` / `get_rare_flavor_pairs()` results are
  sane: salty dominates the common end (matches salt's known ubiquity
  across virtually every recipe), fermented_funky is rarest.
  `get_never_paired_flavors()` correctly returns `[]` given the row count
  already equals the theoretical max.
- Confirmed no side effects on other tables: `recipes` still 54,167,
  `ingredient_pairs` still 1,016,901, `ingredient_flavors` still 18,887,
  `meal_plans` still 0.

Checked off Phase 12's pairing-stats item in PLAN.md and committed the
resulting `recipes.db` change (the dispatch had run it against production
but left the DB change uncommitted). Remaining Phase 12 items: surfacing
flavor data in suggestions (integrate-with-existing - `suggest_companions()`
in `meal_planner.py` is an existing function, so per this project's
established heuristic this gets built directly rather than dispatched) and
the explicitly-deferred external flavor-dataset research (not required for
Phase 12 completion, deferred by design per SPEC.md).

## 2026-07-09 (scheduled check-in) - Phase 12: flavor-pair stats dispatched (IN PROGRESS)

No background container was running at the start of this run (`docker ps
--filter name=openclaw-cli-run` empty). Independently re-verified the prior
"flavor queries complete, STATUS.md DONE" entry against ground truth before
trusting it: git HEAD (913c37b) matched PROGRESS.md, working tree clean,
`flavor_queries.py`/`flavor_tagging.py`/`flavor_taxonomy.py` all present, and
direct SQL against production `recipes.db` confirmed every claimed count
exactly - `recipes`=54167, `ingredient_flavors`=18887, `flavor_categories`=17,
distinct `ingredient_flavor_tagged`=8568, `ingredient_totals`=71882,
`ingredient_pairs`=1,016,901. No drift found.

Took the next unchecked PLAN.md item: Phase 12's "Flavor-profile-level
pairing stats (common/rare/never-paired combinations), rolled up from
existing ingredient-pairing data." Inspected the real schema first rather
than assuming: `ingredient_pairs(ingredient_a, ingredient_b, pair_count)`
and `ingredient_flavors(ingredient, flavor)` are both flat two/three-column
tables, so this is a pure rollup - for each ingredient pair, cross the two
ingredients' flavor tag sets and accumulate `pair_count` onto each
resulting (flavor_a, flavor_b) combination (self-pairs like sweet+sweet
kept, since two independently-sweet ingredients being used together is a
real, meaningful signal, not noise to filter out).

Classified as net-new (new file `flavor_pairing.py`, new table
`flavor_pair_stats` it creates itself, only SELECT-reads against the three
existing tables above, no edits to any existing module) per this project's
established dispatch heuristic. Dispatched to OpenClaw, session key
`agent:main:sous-flavor-pairing-<timestamp>`, with an exact function-by-
function spec (four functions: `rebuild_flavor_pair_stats`,
`get_flavor_pair_count`, `get_common_flavor_pairs`, `get_rare_flavor_pairs`,
`get_never_paired_flavors`) and a concrete, falsifiable verification script
built into the prompt: scratch-copy-first checks (idempotency re-run,
literal printed output at each step, no hardcoded expected values since the
actual flavor-pair numbers are genuinely unknown ahead of time), only then
the real run against production `recipes.db`, with an explicit instruction
not to touch `recipes.db`'s `recipes` table (checked before/after, must stay
54,167) and not to stage/commit the data file itself. Confirmed via real
`docker ps` output (not assumed) that container
`openclaw-in-docker-openclaw-cli-run-495ef00c04ee` is actually running
(`Up 7 seconds (healthy)`) before ending this turn.

**Left deliberately unfinished, correctly**: the dispatch is in flight as
this entry is written - not yet verified, not yet checked off in PLAN.md.
`STATUS.md` set to `IN_PROGRESS` (was `DONE`, correctly updated to reflect
this in-flight work). **Next check-in must independently verify this
before trusting it or touching PLAN.md**: confirm the container isn't
still running (or exited cleanly), check that `flavor_pairing.py` actually
exists and was committed, re-run the same verification queries directly
against real `recipes.db` (not the dispatch's own self-reported output, per
this project's standing pattern of catching false or incomplete claims
from this local model on prior dispatches) - especially confirm
`flavor_pair_stats` row count is sane (at most 17*18/2=153 possible
unordered flavor-pair combinations including self-pairs) and that
`recipes` is still exactly 54,167 rows untouched.

## 2026-07-09 (operator check-in) - Phase 12: flavor queries complete

The 14:00 scheduled cycle's dispatch (`flavor_queries.py`, session key
`...b666be82c85a`) died silently with zero trace - no running container,
no exited container, no file on disk. This is the third silent dispatch
death this phase (twice previously during ingredient tagging). Rather than
let the next 30-minute cron cycle re-dispatch into the same failure
pattern, built `flavor_queries.py` directly (operator, not OpenClaw) using
the exact spec already logged in the prior entry (four functions:
`get_ingredient_flavor_profile`, `get_recipe_flavor_profile`,
`get_cuisine_flavor_profile`, `get_meal_plan_flavor_profile`).

Confirmed the ingredient-to-flavor join needs no fuzzy matching (same
`.strip().lower()` normalization used by `ingredient_flavors` and
`meal_planner.rebuild_ingredient_pairs()`), and that `cuisine` is a
comma-separated multi-value column, so `get_cuisine_flavor_profile` splits
on commas rather than doing a raw exact/substring match against the whole
column.

**Verified against the same hardcoded expected values the dead dispatch
was supposed to be checked against**, run for real, not paraphrased:
- Recipe 1226 (blueberries/granulated sugar/vanilla yogurt/lemon juice):
  `ingredient_count=4`, `untagged_ingredients=[]`, `flavor_counts` exactly
  `{sweet:3, floral:2, sour:2, citrus:1, fresh_green:1, salty:1}` - matches.
- Cuisine `italian`: `recipe_count=4480` - matches (component-split match,
  not the naive exact-match count of 4,396 noted in the prior entry).
- Recipe 1227 (Biryani): 2 ingredients correctly reported untagged
  (`cardamom seed`, `hot green chili peppers`), consistent with the known
  tagging-coverage gap (only ingredients with `total_count >= 3` were
  tagged).
- Built a real scratch meal plan (`create_plan`/`add_recipe_to_plan` via
  the actual `MealPlanDatabase` API) combining recipes 1226+1227, called
  `get_meal_plan_flavor_profile`, and confirmed its `flavor_counts` exactly
  equals the sum of the two recipes' independently-fetched
  `flavor_counts` (`Counter` addition, not just eyeballed).
- Deleted the scratch plan afterward; confirmed via direct SQL that
  `recipes` is still exactly 54,167 rows and no `flavor-verify-scratch`
  plan remains in `meal_plans`.

Checked off Phase 12's "Ingredient/dish/meal/cuisine-level flavor queries"
item in PLAN.md and committed. STATUS.md set back to `DONE`. Remaining
Phase 12 items: flavor-profile-level pairing stats, surfacing flavor data
in suggestions (integrate-with-existing, likely a direct build against
`suggest_companions()` rather than a dispatch), and the deferred external
flavor-dataset research. Phase 13 (easter eggs) is still fully open.

**Worth flagging as a pattern, not just a one-off**: three silent dispatch
deaths in one afternoon (this one, plus two earlier during flavor tagging)
is a real reliability signal about this local-model dispatch setup under
sustained multi-hour use, not just isolated bad luck - if it keeps
recurring, worth investigating root cause (timeout, OOM, container resource
limits) rather than just absorbing it as a per-cycle cost each time.

## 2026-07-09 (scheduled check-in, later still x2) - Phase 12: flavor query module dispatched (IN PROGRESS)

No background container was running at the start of this run (`docker ps --filter name=openclaw-cli-run` and `-a` both empty). Independently re-verified the prior "flavor-tagging complete" entry against ground truth before trusting it: direct SQL against production `recipes.db` confirmed `ingredient_flavor_tagged` = 8,568 (matches `ingredient_embeddings`), `ingredient_flavors` = 18,887 rows with exactly the 17 taxonomy flavor names present (no invented tags), `flavor_categories` = 17 rows, `recipes` = 54,167 rows, git HEAD (bc240b4) and working tree clean - no drift. Proceeded to the next unchecked PLAN.md item: "Ingredient/dish/meal/cuisine-level flavor queries."

Investigated the actual linkage before designing anything: `ingredient_flavors.ingredient`/`ingredient_flavor_tagged.ingredient` use the exact same normalization (`.strip().lower()`) as `ingredient_totals.ingredient` in `meal_planner.py`'s `rebuild_ingredient_pairs()`, which is itself built directly from `recipes.ingredients` (a JSON list of free-text strings) - so a recipe's ingredients can be joined to flavor tags with simple string normalization, no fuzzy matching needed. Confirmed concretely against real recipe 1226 (blueberries/granulated sugar/vanilla yogurt/lemon juice - all 4 tagged, yields sweet:3/floral:2/sour:2/citrus:1/fresh_green:1/salty:1) and recipe 1227 (Biryani, 25 ingredients, 2 untagged e.g. "hot green chili peppers" - a real example of the known coverage gap, since tagging only covered ingredients with total_count >= 3). Cuisine field can hold comma-separated multi-values (e.g. "italian, french"), confirmed via direct SQL: exact-match `cuisine='italian'` finds 4,396 recipes but component-split match finds 4,480 (the correct, non-arbitrary predicate).

Classified as net-new (a new file, pure SELECT-only query functions reading existing tables - `recipes`, `ingredient_flavors`, `ingredient_flavor_tagged`, `meal_plans`/`meal_plan_items` via the existing `MealPlanDatabase` CRUD API - no editing of any existing module's internals) per this project's established dispatch heuristic. Dispatched `flavor_queries.py` (four functions: `get_ingredient_flavor_profile`, `get_recipe_flavor_profile`, `get_cuisine_flavor_profile`, `get_meal_plan_flavor_profile`) to OpenClaw, session key `agent:main:sous-flavor-queries-1783605733245346934`, with an exact field-by-field spec and a concrete, falsifiable verification script built into the prompt (hardcoded expected values for recipe 1226, cuisine 'italian' recipe_count=4480, and a real scratch meal-plan combining recipes 1226+1227 whose combined flavor_counts must exactly equal the independently-summed per-recipe profiles). Confirmed via real `docker ps` output (not assumed) that container `openclaw-in-docker-openclaw-cli-run-b666be82c85a` is actually running, healthy, before ending this turn.

**Left deliberately unfinished, correctly**: the dispatch is in flight as this entry is written - not yet verified, not yet checked off in PLAN.md. `STATUS.md` stays `IN_PROGRESS` (already correct, no drift to fix this time). **Next check-in must independently verify this before trusting it or touching PLAN.md**: confirm the container isn't still running (or exited cleanly), check that `flavor_queries.py` actually exists, re-run the same verification checks directly against real `recipes.db` (not the dispatch's own self-reported output, per this project's standing pattern of catching false claims from this local model on prior dispatches this phase) - especially check 5 (meal-plan aggregation) and check 6 (scratch plan actually cleaned up, `recipes.db` still exactly 54,167 rows) since those involve real writes to `meal_plans`/`meal_plan_items` that must be verified as reverted.

## 2026-07-09 (scheduled check-in, later still) - Phase 12: ingredient flavor-tagging complete, verified

No background container was running (`docker ps --filter name=openclaw-cli-run` and
`-a` both empty - the resumed dispatch from the prior entry had already exited,
again with no error trace). Per standing rule, queried `recipes.db` directly rather
than trusting the prior entry's "in flight, ~25 min estimated" claim:
`ingredient_flavor_tagged` was at 7,200/8,568 - real progress since the last check
(4,110 -> 7,200), but the background container had died silently again before
finishing, the same failure signature as the previous attempt.

Remaining work was small (1,368 ingredients, ~7 min at the established batch pace),
so rather than dispatching a third background container into the same silent-death
pattern, ran `tag_all_ingredients()` directly in the foreground this turn (it's the
same already-verified, idempotent, resumable function - correctly skipped the 7,200
already-tagged rows and picked up where the container left off). Ran to completion
with progress prints (300/1308, 600/1308, 900/1308, 1200/1308) and exited normally.

**Verified for real via direct SQL against production `recipes.db`, not the
script's own print output**: `ingredient_flavor_tagged` = 8,568 (exact match to
`ingredient_embeddings`); `ingredient_flavors` now has 18,887 rows; all 17 distinct
`flavor` values present are valid taxonomy names (zero invented tags, e.g. no
repeat of the earlier "scented"/"fishy" leaks); `flavor_categories` still exactly
17 rows; `recipes` still exactly 54,167 rows (untouched). No leftover scratch
`.db` files found (`find` for `*scratch*`/`*.db` outside `recipes.db` came back
empty).

Checked off PLAN.md's "LLM-tag all ~8,568 embedded ingredients against the
taxonomy" item. STATUS.md stays IN_PROGRESS - Phase 12 has three items left
unchecked (flavor queries, flavor-pairing stats, surfacing in suggestions).

**Next check-in**: take the next unchecked Phase 12 item, "Ingredient/dish/meal/
cuisine-level flavor queries" - this is a new module querying the now-complete
`ingredient_flavors`/`flavor_categories` tables, likely net-new (new query
functions in a new or existing-but-additive module) so worth trying a dispatch
first per the established heuristic, unless it ends up needing to join through
existing recipe-rendering code in server.py, which would make it
integrate-with-existing instead.

## 2026-07-09 (scheduled check-in, later) - Phase 12: found two real drifts, corrected both, resumed tagging (IN PROGRESS)

No background container was running (`docker ps --filter name=openclaw-cli-run` empty, both with and without `-a` - the prior dispatch's container was already gone entirely, consistent with `docker compose run --rm` auto-removing on exit rather than lingering as a stopped container). Per this project's standing rule to verify claims independently before trusting STATUS.md/PROGRESS.md, queried the real `recipes.db` directly instead of assuming the prior entry's in-flight dispatch had finished (or even that it was still running).

**Found two real, independently-confirmed drifts:**
1. **The full tagging run did not complete.** `ingredient_flavor_tagged` had only 4,110 rows, not the full 8,568 (`ingredient_embeddings` count). The container had already been removed (`--rm`) with no error trace left behind - it silently stopped partway through with no record of why. Good news found in the same check: all 17 distinct `flavor` values present in `ingredient_flavors` (7,539 rows at that point) are valid taxonomy names, no invented ones leaked through, and `recipes.db` was untouched at 54,167 rows - so the partial progress itself was clean, just incomplete.
2. **The `flavor_categories` table did not exist in production `recipes.db` at all**, despite PLAN.md's Phase 12 first checklist item ("define the flavor taxonomy") being checked off and an earlier PROGRESS.md entry describing `seed_flavor_taxonomy()` as verified. Re-reading that entry and the commit (e763d94) confirmed why: verification was done entirely against scratch DB copies, and the real seed call was never actually run against production - "real recipes.db untouched" was true but meant the deliverable (the table existing for real) was never actually applied, not just untested.

**Corrected both directly, not via dispatch** (both are net-new/idempotent single-function calls against already-verified code, not integration work):
- Ran `seed_flavor_taxonomy('recipes.db')` for real against production, twice in a row to confirm idempotency. Verified via the module's own `get_flavor_categories()` and direct SQL: `flavor_categories` now has exactly 17 rows (5 `basic_taste`, 12 `aromatic`), no duplicates after the second call, `recipes.db` still 54,167 recipe rows.
- Resumed the interrupted tagging job by redispatching `tag_all_ingredients()` (its resumable design skips the 4,110 already-tagged ingredients automatically, so this correctly continues rather than restarting) to a fresh background container, session key `agent:main:sous-flavor-tag-resume-<timestamp>`. Container `openclaw-in-docker-openclaw-cli-run-5cc37c7edfe3` confirmed actually running via real `docker ps` output (`Up 5 seconds (healthy)`) before ending this turn, not assumed. Estimated ~25 min for the remaining ~4,458 ingredients.

**Left deliberately unfinished, correctly**: the tagging run is in flight again as this entry is written. PLAN.md's tagging checklist item stays unchecked. **Next check-in must independently verify** (not trust any self-report): `ingredient_flavor_tagged` count = 8,568 exactly, `ingredient_flavors` distinct flavor values all within the 17-name taxonomy, `recipes` count still 54,167, `flavor_categories` still exactly 17 rows, and check for any leftover scratch `.db` files this dispatch might create.

## 2026-07-09 (scheduled check-in) - Phase 12: ingredient flavor-tagging dispatched (IN PROGRESS)

No background agent was running at the start of this run (`docker ps
--filter name=openclaw-cli-run` empty). Independently re-verified the prior
Phase 12 taxonomy entry against ground truth first: git HEAD (e763d94)
matched PROGRESS.md, working tree clean - no drift, proceeded to the next
unchecked PLAN.md item: "LLM-tag all ~8,568 embedded ingredients against
the taxonomy."

Built `flavor_tagging.py` directly myself (Read/Edit, not dispatched) -
this involves LLM-prompt design and response-parsing/validation nuance,
not simple net-new boilerplate, so didn't fit the "net-new -> dispatch"
heuristic cleanly. Checked available local Ollama models
(`curl .../api/tags`): `qwen3:8b` chosen over the bigger `gemma4:26b` for
throughput reasons (see timing below), over embedding-only models which
can't do this. Benchmarked directly against the real Ollama endpoint
before committing to an approach: default (thinking-mode) calls took ~20s
per batch of 10 ingredients; passing `"think": false` cut that to ~3.3s/10
and ~8.6s/30 with no meaningful quality loss (same real ingredients got
sensible tags either way) - chose batch_size=30 with think:false, giving
an estimated ~40 min for the full 8,568-ingredient pass (286 batches).

`flavor_tagging.py`: `tag_ingredient_batch()` POSTs a numbered-list prompt
to `/api/chat` (format=json, think=false), parses the JSON response,
drops any tag not in the 17-name `flavor_categories` taxonomy (caught the
model actually doing this in testing - it invented "scented" and "fishy"
in two different test batches, both correctly filtered out).
`tag_all_ingredients()` mirrors `embeddings.py`'s resumable pattern
exactly: new `ingredient_flavors` (ingredient, flavor) junction table plus
a separate `ingredient_flavor_tagged` marker table (needed because an
ingredient can legitimately get zero tags - e.g. non-food footnote
strings like "*available at some supermarkets" - so tag *presence* alone
can't signal "already processed").

**Verified on a scratch DB before running anything for real**: built a
throwaway 5-row `ingredient_embeddings` table (not a copy of the real
373MB recipes.db - too large to justify for a 5-row test), ran
`tag_all_ingredients()` against it once (5 tagged, one real Ollama call),
confirmed via direct SQL: `garlic, minced` -> pungent/salty/umami, `lemon
juice` -> citrus/fresh_green/sour, `smoked paprika` -> smoky/sweet/
warm_spice, `sugar` -> salty/sweet, and the footnote string -> zero rows
in `ingredient_flavors` but present in `ingredient_flavor_tagged` (marked
done, not stuck retrying forever). Re-ran a second time: 0 tagged, 0 new
calls (resumability confirmed). Deleted the scratch DB. Committed
`flavor_tagging.py` (73962a6) - not yet run against the real recipes.db in
that commit, deliberately, since the real run is a long batch job.

**Dispatched the real full run to OpenClaw as a background container**,
per this run's efficiency constraint (a ~40-minute local-Ollama batch job
doesn't fit inside one cron cycle's turn, and doesn't need to - it costs
nothing against the Claude budget and the existing `docker ps --filter
name=openclaw-cli-run` detection this project's check-ins already use is
exactly the right mechanism to let it survive across cron ticks).
Session key `agent:main:sous-flavor-tag-run-1783600490543479779` (fresh,
per this project's standing rule). Instructed it explicitly not to edit
`flavor_tagging.py` (already correct and committed), just execute
`tag_all_ingredients()` to completion, then verify via four literal SQL
query outputs (not paraphrased) and explicitly told not to stage/commit
`recipes.db` (untracked data file, not code). Confirmed via real `docker
ps` output (not assumed) that container
`openclaw-in-docker-openclaw-cli-run-86742cbd0b1d` is actually running
before ending this turn.

**Left deliberately unfinished, correctly**: the actual full-batch tagging
run itself is in flight in that container as this entry is written - not
yet verified, not yet checked off in PLAN.md. `STATUS.md` set to
`IN_PROGRESS` (previously stale "DONE" left over from Phase 11, unedited
since 2026-07-08 - a real drift risk this entry corrects). **Next
check-in must independently verify this before trusting it done or
touching PLAN.md**: confirm the container isn't still running (or has
exited cleanly), then directly re-run the same four SQL checks (
`ingredient_flavor_tagged` count = 8568, `ingredient_flavors` row count
and distinct-flavor values all within the 17-name taxonomy,
`recipes` count still 54,167 and no leftover scratch `.db` file) against
the real `recipes.db` - do not trust the dispatched container's own
self-reported claim, per this project's standing pattern of catching
false claims from this local model on every prior dispatch this phase.

## 2026-07-09 (scheduled check-in) - Phase 12: flavor taxonomy defined

No background agent was running (`docker ps --filter name=openclaw-cli-run`
empty). Independently re-verified the Phase 11 DONE state before starting:
git HEAD (96b7005) matched PROGRESS.md's latest entry, working tree clean,
recipes.db exactly 54,167 rows - no drift found.

Took the first unchecked PLAN.md item: Phase 12, "define the flavor
taxonomy." Per SPEC.md Phase 7's already-decided scope (basic tastes:
sweet/sour/salty/bitter/umami; aromatic/other: citrus/earthy/smoky/floral/
pungent/spicy-heat/etc.), finalized the "etc." into a concrete 17-entry
taxonomy (5 basic tastes + 12 aromatic: citrus, earthy, smoky, floral,
pungent, spicy_heat, herbal, nutty, fatty_rich, fresh_green,
fermented_funky, warm_spice) - a design decision made directly per this
run's "don't re-ask about scoped tradeoffs" instruction, not a new
tradeoff requiring a pause.

Classified as net-new (new file `flavor_taxonomy.py`, new table it creates
itself, no editing of existing modules) per this project's established
dispatch heuristic, and dispatched to OpenClaw with an exact schema/
function spec and a concrete, falsifiable verification script built into
the prompt.

**The dispatch's self-report was false on one specific claim, caught by
not trusting it at face value - same standing pattern as prior Paprika/
bulk-import dispatches on this project.** It reported "the test database
file has been deleted" but the transcript itself contained an
unexplained `copy recipes.db to test_data/scratch_flavor_verify.db
failed` line contradicting that. Independent check via `ls` found a
373MB `test_data/scratch_flavor_verify.db` still present (never
deleted). Deleted it. The file content itself was correct, and the real
`recipes.db` was untouched (54,167 rows, no `flavor_categories` table
leaked in) - this was a false cleanup claim, not a content or
production-data bug.

**Verified for real** by independently re-running the exact seed/query
functions against a fresh scratch copy of recipes.db (not trusting the
dispatch's own paraphrased output): `FLAVOR_TAXONOMY` has exactly 17
entries; `seed_flavor_taxonomy()` called twice in a row (idempotency
check) still yields exactly 17 rows via direct SQL, no duplicates;
`get_flavor_categories(group='basic_taste')` returns exactly 5 rows,
`group='aromatic'` returns exactly 12; sample rows have correct
name/category_group/description fields. Scratch DB deleted after.
Real recipes.db confirmed untouched throughout (54,167 rows, no stray
table) both before and after.

**Not done in this pass, deliberately scoped to just this one PLAN.md
checklist item**: no ingredient tagging yet (that's Phase 12's next
item, "LLM-tag all ~8,568 embedded ingredients against the taxonomy") -
left as the next unchecked item, not forgotten.

## 2026-07-09 (scheduled check-in) - Phase 11 complete: print view customization

No background agent was running (`docker ps --filter name=openclaw-cli-run`
empty). Git log (1d718f7) and working tree (clean) matched PROGRESS.md/
PLAN.md with no drift, so proceeded to the next unchecked item.

Classified as integrate-with-existing (editing `serve_print_view()`, an
existing function in `server.py`, plus the `/print` route in `do_GET`) -
per this project's established heuristic, this category has failed
reliably on the local model all project, so built it directly (Read/Edit)
rather than dispatching.

Added four toggles via query params on `/print` (`layout=standard|
compact`, `font=normal|large`, `images=0|1`, `nutrition=0|1`), controlled
by a `no-print` form on the print page itself (select/checkbox inputs that
auto-submit via `onchange`, GET method so the toggle state lives in the
URL and survives a refresh/print). Checkbox state round-trips correctly
via a hidden-input + checkbox pair sharing the same name (browsers send
both; the code takes the *last* value, so unchecked -> the hidden `0`
wins, checked -> the checkbox's `1` overrides it) - verified this
mechanism actually works via curl rather than assuming standard HTML
form semantics apply as expected. Invalid/unrecognized param values fall
back to the default instead of erroring.

**Honest scope note, caught by checking the schema before building, not
after**: the `recipes` table has no `image_url` or `nutrition` column at
all (`PRAGMA table_info` confirmed only the 15 existing columns), so the
`images` and `nutrition` toggles are fully functional but currently always
no-ops for every one of the 54,167 recipes - toggling "Show image" or
"Show nutrition" on renders an honest `(no image/nutrition data available
for this recipe)` note (itself `no-print`, so it never appears on an
actual printed page) instead of silently doing nothing. `font` and
`layout` are fully functional today since they're pure CSS, not data-
dependent. This matches SPEC.md Phase 6's already-decided toggle set
exactly (images/font/nutrition/layout), so not a new tradeoff requiring a
pause - just documenting the current data gap honestly, same pattern as
the `total_time=0` limitation noted earlier in this file.

**Verified live through the real running server** (port 8126, since 8000
was occupied by an unrelated process): tested against real recipe 1226 -
default state (images checkbox checked, nutrition unchecked, standard/
normal selected) renders the no-image note and no nutrition section;
`font=large` injects the 17px/14pt override; `layout=compact` injects the
tightened-spacing CSS; `images=0` correctly suppresses the no-image note
entirely; `nutrition=1` correctly shows the no-nutrition note;
`layout=compact&font=large` both selects render with `selected` on the
right `<option>`; invalid values (`font=huge&layout=weird`) fall back
cleanly (200, not a crash); nonexistent recipe id still 404s; ingredients/
instructions still render correctly (feature didn't regress the base
print view). Confirmed the recipe view's existing "Print Recipe" link
(`/print?id=1226`) still resolves. This feature is read-only (all state
lives in the URL query string, nothing written to the DB) - confirmed
recipes.db unchanged at exactly 54,167 rows before and after.

**Phase 11 (Recipe Utility) is now fully complete**: recipe scaling
(scaling logic verified, though still not wired into the recipe-view UI -
see What's Next) and print view customization.

## 2026-07-09 (scheduled check-in, later) - Phase 11: recipe scaling

No background agent was running (`docker ps --filter name=openclaw-cli-run`
empty). Independently re-verified Phase 10's DONE state against ground
truth before starting: `recipes.db` exactly 54,167 rows, `recipe_notes`/
`cook_log` both 0 rows (matches PROGRESS.md's cleanup claim), `git log`
head (3e332f6) matched too - no drift found.

Took the first unchecked PLAN.md item: recipe scaling (Phase 11, SPEC.md
Phase 6). Ingredients are stored as free-text strings (`recipe_model.py`
has no structured quantity/unit/item split), so this requires parsing a
leading quantity out of text via heuristics.

Classified as net-new (a new file, pure functions, no dependency on
existing modules or the DB) per this project's established dispatch
heuristic, and dispatched `recipe_scaling.py` to OpenClaw with an exact
function-by-function spec and a concrete, falsifiable verification script
built into the prompt. **Failed twice in a row on fresh sessions**, both
times with the same "LLM request failed" signature seen elsewhere this
project - confirmed via `ls` after each attempt that no file was written
either time (a clean failure, nothing to clean up). Per the established
retry-once-then-build-directly rule, built it directly (Read/Write) after
the second failure.

**A real bug found and fixed during my own verification, not just
compiled**: the first version of `parse_quantity()` used one combined
regex with optional whole-number and fraction groups. For input like
`"1/2 tsp salt"`, the whole-number group greedily consumed the `1`
before the fraction group could match `1/2`, leaving `/2 tsp salt` as an
orphaned remainder - `scale_ingredient('1/2 tsp salt', 2)` produced
`'2 /2 tsp salt'` instead of `'1 tsp salt'`. Caught by actually running
the verification script rather than assuming the regex was correct.
Rewrote as an ordered sequence of specific patterns (decimal, mixed
number, simple fraction, whole+unicode-fraction, unicode-fraction-alone,
plain integer/range) tried in that order instead of one combined regex.

**Verified for real by running the exact test script** (decimal, simple
fraction, mixed number, unicode fraction attached to a whole number,
unicode fraction alone, a "3-4"-style range, a no-quantity case, division-
by-zero guard, non-mutation of the input list):
- `'1/2 tsp salt'` * 2 -> `'1 tsp salt'` (the bug above, now fixed)
- `'1 1/2 cups sugar'` * 2 -> `'3 cups sugar'`
- `'1¾ cups milk'` * 2 -> `'3 1/2 cups milk'`
- `'½ cup butter'` * 3 -> `'1 1/2 cup butter'`
- `'3-4 cloves garlic'` * 2 -> `'6 cloves garlic'` (takes the first number)
- `'salt to taste'` * 2 -> `'salt to taste'` (unchanged, no quantity found)
- `scale_recipe_to_servings(['2 cups flour'], 0, 4)` -> `['8 cups flour']`
  (current_servings<=0 guarded to 1, doesn't raise ZeroDivisionError)
- `scale_ingredients()` confirmed non-mutating: original list unchanged
  after the call.

**Known, honestly-documented cosmetic limitation, not a bug**: unit-glued
quantities like `'400g spaghetti'` scale correctly in value but the
reformatted output inserts a space (`'600 g spaghetti'`) since
`format_quantity()` + reassembly doesn't preserve the original's lack of
a space between number and unit. The number itself is correct.

Not yet wired into server.py (no UI to enter a scale factor / target
servings on the recipe view) - that's integrate-with-existing work
(editing the existing recipe-view route/rendering), left as a deliberate
follow-up for a future pass per this project's established heuristic,
not forgotten. Print view customization (the other Phase 11 item) also
still open.

## 2026-07-09 (scheduled check-in) - Phase 9: generic bulk schema.org-shaped JSON import

No background agent was running (confirmed via `docker ps --filter
name=openclaw-cli-run` - empty). Independently re-verified the prior Paprika
import DONE claim against ground truth first (recipes.db at exactly 54,167
rows, recipe_categories at exactly 1,223 rows, matches PROGRESS.md/PLAN.md;
git log head matched too) - no drift found, proceeded to the next unchecked
PLAN.md item.

Found a leftover untracked fixture from an earlier, apparently interrupted
session: `test_data/sample_bulk_recipes.json` (2 schema.org-shaped recipes
with known expected field values), with no corresponding import script yet.
Reused it rather than rebuilding from scratch.

Built `import_bulk.py` (new file): imports a JSON array of schema.org-shaped
recipe dicts, reusing `import_url_recipe.py`'s existing `extract_recipe_data()`
(same HowToStep-flattening/servings-parsing logic already used for single-URL
import) plus `categories.add_category()` for `recipeCategory`. Classified as
net-new (new file calling existing modules' public API, not editing their
internals) per this project's established dispatch heuristic, and dispatched
to OpenClaw with an exact field-by-field spec and a concrete, falsifiable
verification script built into the prompt.

**The dispatch's self-report could not be trusted at face value - again -
but this time it caused real damage, not just an inflated claim.** The agent
reported success with a prose summary but never showed the actual command
output it was explicitly told to include. Independent verification found:
1. It had **deleted the entire `test_data/` directory**, including the
   tracked, permanent regression-test fixture `test_data/sample.paprikarecipes`
   from the Paprika import phase (`git status` showed it as deleted) - its
   cleanup step apparently removed the whole directory instead of just the
   scratch DB copy it was told to delete. Restored via `git checkout --
   test_data/sample.paprikarecipes`; recreated the untracked
   `sample_bulk_recipes.json` fixture from the content already captured
   this session.
2. The `import_bulk.py` code itself was actually correct once restored and
   independently re-run - this was a destructive side effect during
   "cleanup," not a content bug in the deliverable.

**Verified for real, via direct SQL against a scratch copy of recipes.db**:
`import_bulk_file()` returned ids [94846, 94847] (same as the earlier
Paprika-import test ids - expected and not a red flag, since both tests
copy the same production DB and SQLite autoincrement continues from the
same max id regardless of which script ran). Row 94846 (Fixture Lasagna
Bulk): servings=8, cuisine='Italian, American', difficulty='Medium',
license='user-imported', 5 ingredients, 3 instruction steps (confirmed the
HowToStep-with-only-'name'-field fallback produced "Layer noodles and
sauce." correctly), category=['Dinner']. Row 94847 (Fixture Pancakes Bulk):
servings=4, cuisine='American', difficulty='Easy', 4 ingredients, 3
instruction steps (confirmed the plain-string splitting path), categories
sorted to ['Breakfast', 'Brunch']. Confirmed real `recipes.db` was 54,167
rows before and after (untouched) - checked via direct SQL both times, not
the script's own output. Scratch DB copy deleted afterward and confirmed
gone.



## 2026-07-07 20:28 PDT - Initial Setup
- Created project directory structure
- Documented project specification including target users, MVP scope, tech stack, and roadmap
- Created project plan with phases
- Set STATUS.md to IN_PROGRESS

## 2026-07-07 21:15 PDT - Core Implementation
- Implemented recipe data models with SQLite database integration
- Created web server framework using Python's standard library HTTP server
- Built core functionality for recipe CRUD operations (create, read, search)
- Implemented import page and print view
- Created Dockerfile and docker-compose.yml for packaging
- Added requirements.txt for documentation
- Committed all changes to git

## 2026-07-07 21:48 PDT - Dataset Integration Planning
- Downloaded the actual recipes_data_food.com dataset from Hugging Face (6.4MB parquet file)
- Updated SPEC.md with correct dataset information and source URL
- Planned data processing approach for importing ~20,000-30,000 quality recipes
- Prepared to implement proper data import functionality using pyarrow/pandas when available

## 2026-07-07 21:53 PDT - Final Implementation
- Created import_dataset.py script that demonstrates how the dataset would work
- Updated documentation with correct dataset source information
- Finalized project structure and files

## 2026-07-07 21:58 PDT - Real Dataset Import
- Successfully installed pyarrow in virtual environment
- Analyzed dataset structure (1,048,543 rows, 29 columns)
- Imported 1224 recipes from the dataset
- Filtered for quality (non-empty ingredients and instructions)
- Built ingredient co-occurrence model based on real data

## 2026-07-07 22:45 PDT - URL Import Bug Fixes (fixed by operator, not the agent)
After 6 agent attempts on this same fix all failed with the same context-overflow
error, the operator patched import_url_recipe.py directly: (1) @type can be a
list like ["Recipe"], not just a bare string - extract_json_ld_scripts only
checked the bare-string case; (2) it called a nonexistent db.create_recipe(dict)
instead of the real save_recipe(Recipe) API; (3) save_recipe fails if
cuisine/difficulty are lists, which schema.org allows. Verified end-to-end
against a real live URL (simplyrecipes.com/recipes/chocolate_chip_cookies),
not mock data: recipe id 1225, "Chewy Chocolate Chip Cookies", real ingredients.

## 2026-07-07 23:10 PDT - Dataset Expansion (done by operator)
The user correctly flagged that 1,224 recipes was too small. Investigation
found the root cause: the AkashPS11 dataset claims ~1M rows but 99.88% have
NULL ingredients/instructions in this parquet mirror - only ~1,228 rows were
ever usable, which is why the "full" import kept landing on ~1223 regardless
of processing all 1,048,543 rows. Verified two replacement sources for real
null rates before importing (not just row-count claims) - see SPEC.md for
full detail:
- Hieu-Pham/kaggle_food_recipes (MIT): 13,495 recipes imported, 0% nulls.
- datahiveai/recipes-with-nutrition (CC BY-NC 4.0): 39,447 recipes imported,
  0% nulls, real cuisine tags, but no instructions field (ingredients/nutrition
  only).
Added a `license` column to the recipes table so CC-BY-NC entries can be
filtered out later if commercial use ever matters.
Recomputed the co-occurrence model against the full real corpus (also fixed a
bug where the old script's "sanity check" printed hardcoded fake labels like
"basil + tomato" attached to whatever count happened to be in that list
position - the underlying counts were real, the labels were not). Real top
pairs at 54,166 recipes: garlic+salt (5058), pepper+salt (4826), olive
oil+salt (3984), garlic+olive oil (3926), salt+water (3040) - all independently
verified by directly querying the database, not trusting any script's printed
summary.

**Total recipes now: 54,166** (up from 1,224).

## 2026-07-08 09:50 PDT - UI/UX Polish (done by operator, after 3 failed agent attempts)
Three autonomous attempts at this (shared CSS + nav + license field + no-directions
note) all failed with the same "Ollama API stream ended without a final response"
error, even after raising models.providers.ollama.timeoutSeconds from 300 to
1200 and splitting the task smaller. Root cause never fully resolved - see
AUTONOMOUS_BUILD.md. The operator made these edits directly instead:

- server.py: extracted get_base_style() and get_nav_html(), used across all 5
  routes instead of 5 duplicated inline <style> blocks. Nav (Home/Search/
  Import) added to every page except the print view, per spec.
- recipe_model.py: Recipe class was missing the license field even though the
  DB column existed (added directly via SQL earlier, bypassing this class).
  Added license to __init__/to_dict/from_dict/save_recipe/_row_to_recipe,
  verified against the real column order (PRAGMA table_info - license is
  index 14).
- server.py recipe view + print view: if instructions is empty, show
  "Ingredients only - no directions available for this recipe." instead of a
  blank section.

**Two more serious bugs found and fixed along the way, unrelated to the above:**
- server.py had a syntax error (nested f-string reusing the same triple-quote
  delimiter in serve_search) - the file could never have actually run before
  this. Fixed by extracting a recipe_card_html() helper instead of nesting.
- The __main__ block never called run_server() - it only ran a DB smoke test
  and exited. The "web server" has never actually served a single HTTP
  request until now. Fixed to call run_server(port=8000).

**Verified for real, not just compiled:** started the server for real inside
the container and curled it - confirmed nav renders on home/search, is absent
on print view, a real CC-BY-NC recipe (id 55391) shows the no-directions
note, a real recipe with instructions (id 1226) renders them normally, and
the API response includes the license field. Cleaned up one leaked test
recipe that a stale `if __name__` block inserted into the real DB during
verification.

**Known follow-up, not fixed now (out of scope for this pass):** recipe 1225
(the schema.org URL import) shows raw HowToStep dict reprs as instructions
instead of plain text - the URL importer doesn't flatten structured
instruction steps to strings. Cosmetic, only affects that one recipe so far.

## 2026-07-08 10:40 PDT - Wire up real URL import + fix HowToStep flattening (done by operator, 4th failed autonomous attempt)
A 4th autonomous attempt at multi-file code editing failed the same way (LLM
request failed after reading a few files, no changes made) - this now looks
like a structural limit for this category of task on this model/hardware,
not something further config tuning fixes (see AUTONOMOUS_BUILD.md). File-
deletion-only tasks (the earlier cleanup) succeed fine; tasks requiring real
reasoning about and editing multiple files' logic do not. Operator did this
one directly:

- server.py's handle_import_recipe was a placeholder that always returned a
  fake success message without importing anything. Wired it to actually call
  import_url_recipe.py's import_recipe_from_url(url), returning the real
  recipe_id or a real error.
- import_url_recipe.py's extract_recipe_data: fixed HowToStep flattening
  (recipeInstructions can be a list of dict objects with a 'text' field, or
  sometimes only a 'name' field, not just plain strings) - now falls back
  text -> name -> str(item) instead of leaving raw dict reprs in stored data.
- Deleted and re-imported the one pre-existing recipe (1225) that had the
  old broken instructions, since the code fix doesn't retroactively fix
  already-stored data.

**Verified for real, through the actual HTTP API, not just compiled or
called directly:** started the server, POSTed two real never-before-tried
URLs to /api/recipe/import (banana bread and chocolate chip cookies from
simplyrecipes.com), got back real recipe_ids, and confirmed via GET that
instructions render as clean plain text with no raw dict artifacts,
including the specific HowToStep-with-no-'text'-field case that a first
version of the fix missed (caught by checking the actual rendered output,
not just the "no errors" absence of failure). One import attempt hit a
transient network failure on retry; confirmed it was transient by
succeeding immediately after, not a code bug. recipes.db: 54,167 recipes
(both new test imports kept as real, working examples of the feature).

## 2026-07-08 11:15 PDT - Error handling and validation (done by operator, given the established autonomous-task pattern for multi-file code work)
Found and fixed 4 concrete gaps in server.py:
- do_GET: /recipe/<id> and /print?id= both did a bare int() conversion with
  no error handling - a non-numeric id would crash the request instead of
  returning a clean error. Now returns 400 "Invalid recipe id".
- handle_create_recipe and handle_import_recipe both parsed the request body
  (Content-Length, JSON) *outside* their try/except blocks - a missing body,
  missing Content-Length header, or malformed JSON would raise an unhandled
  exception instead of a clean error response. Extracted a shared
  _read_json_body() helper that returns a proper 400 with a real message.
- handle_create_recipe now requires a non-empty title (400 "title is
  required" otherwise) - previously it would silently create a titleless
  recipe.
- handle_import_recipe now requires a non-empty url (400 "url is required")
  instead of passing an empty string through to the importer.

**Verified live against the real running server**, not just compiled: tested
all 4 fixes (invalid recipe id, invalid print id, missing body, empty title,
missing url, malformed JSON) and confirmed each returns 400 with a real
error message, then confirmed the happy paths (GET a real recipe, POST a
valid new recipe) still return 200/201 correctly. Test data cleaned up
afterward; recipes.db back to 54,167.

Also closed a real gap against SPEC.md's stated V1 scope ("Recipe CRUD
operations: browse, search, view, save, update, delete") - update and delete
existed in RecipeDatabase but had no HTTP route at all. Added do_PUT/
do_DELETE handlers (PUT/DELETE /api/recipe/<id>). Verified against the real
database (not the API response alone, which is easy to fake): created a test
recipe, updated it, confirmed the new title/servings actually persisted via
a direct SQL query, deleted it, confirmed the row was actually gone via a
direct SQL query. Also confirmed updating/deleting a non-existent id
correctly returns 404 rather than a crash.

**V1 scope (per SPEC.md) is now genuinely complete**: recipe CRUD (all six
operations, all with real HTTP routes), print view, dataset import (54,167
recipes from 3 verified sources), co-occurrence pairing model, real
schema.org URL import, Docker packaging, error handling/validation.

## 2026-07-08 (scheduled check-in) - Verified V1 DONE claim, no code changes
No background agent was running. Independently re-verified the prior
"DONE" status against ground truth rather than trusting it, per standing
distrust of unverified claims in this project:
- Started server.py for real (port 8123, since 8000 was occupied by an
  unrelated container, `stairs-api`) and hit live endpoints, not mocks.
- Confirmed recipes.db has exactly 54,167 rows (matches PROGRESS.md).
- Confirmed nav renders on `/` and `/search`, is absent on `/print`.
- Confirmed recipe 55391 shows the no-directions note and recipe 1226
  renders real instructions (both claims from the 09:50 entry).
- Confirmed `license` field is present in `/api/recipes` output.
- Confirmed validation: non-numeric `/recipe/<id>` and `/print?id=` both
  return 400; empty title on create and empty url on import both return
  400 with real error messages.
- Confirmed full CRUD end-to-end: created a recipe via POST, updated it via
  PUT, confirmed the new title/servings actually persisted via a direct SQL
  query, deleted it via DELETE, confirmed the row was actually gone via a
  direct SQL query.

## 2026-07-08 13:45 PDT - Meal Planning Implementation (done by operator)
The agent attempted to implement meal planning but failed due to an issue with
the Ollama API. The operator took over to implement the feature:

**Identified and fixed**:
- `recipe_model.py` had a bug in `get_ingredient_pairs()` where it was using
  `self.ingredients` instead of the passed-in ingredients parameter, causing
  it to return empty results when called from the meal planner.
- The agent's attempt to implement this feature failed due to an issue with
  the Ollama API connection timing out during model loading.

**Built**:
- `meal_planner.py` (new file): `MealPlanDatabase` with meal_plans/
  meal_plan_items/recipe_steps/ingredient_pairs/ingredient_totals tables;
  `extract_step_duration()` and `classify_step_type()` (regex + keyword
  heuristics - documented in the module docstring as real approximations,
  not true recipe understanding); `backward_schedule_recipe()` (per-recipe
  timeline) and `backward_schedule_plan()` (merges every recipe in a plan
  into one timeline, flags active-step conflicts between recipes rather
  than attempting to auto-resolve a full multi-recipe optimal schedule -
  a deliberate, honest scope cut, not an oversight); `suggest_companions()`
  (normalized ingredient pairing + cuisine match + difficulty/time
  complement).
- `server.py`: `/plans` (list + create), `/plan/<id>` (view: recipes,
  suggestions, cooking timeline), POST `/api/plan`, POST `/api/plan/<id>/
  recipe`, DELETE `/api/plan/<id>/recipe/<recipe_id>`, DELETE `/api/plan/
  <id>`. Nav updated with a "Meal Plans" link.

**Verified live through the real running server, not just compiled**:
created a real plan (target eat time 18:00), added two real recipes
(a dessert with a "45 minutes" step and a "freeze overnight" step, and a
chicken dish), confirmed the combined backward-schedule timeline correctly
interleaved both recipes' steps by actual start time, both independently
finishing exactly at 18:00, with no false-positive conflict (their active
windows genuinely don't overlap). Then added a third real recipe (Biryani)
whose active steps land in the same window as the chicken dish's, and
confirmed conflict detection actually fires - not just an untested code
path, a real positive case. Deleted the test plan afterward and confirmed
via direct SQL that `meal_plans`/`meal_plan_items` are empty again.

One real data limitation surfaced and left honestly documented rather than
worked around: `total_time` is 0 for all 54,167 recipes (the earlier bulk
import's time-parsing never actually matched), so per-step duration relies
entirely on regex-extracted explicit mentions ("bake for 25 minutes") with
a 5-minute default otherwise - this is a real approximation, not a precise
schedule, and the UI says so.

## 2026-07-08 (later still) - Phase 3: Embedding-based pairing, in progress

User picked the recommended path: use the Ollama embedding models already
running on the user's own LAN box (nomic-embed-text, mxbai-embed-large) -
zero new cost, zero heavy new dependencies, stays fully local. Also
revised down from an earlier plan to use numpy: at only ~8,500 vectors,
pure-Python cosine similarity is fast enough, so this stays dependency-free
(stdlib only), closer to the project's original minimal-deps intent.

**Operating model change applied for real this time**: per explicit user
feedback ("manage openclaw moreso than doing things yourself"), this phase
is being built as a sequence of narrow, single-purpose dispatches to
OpenClaw rather than one broad build done directly:

- Dispatch 1 (embeddings.py: get_embedding() + cosine_similarity()) -
  succeeded on the first attempt. Independently verified by re-running it
  myself: exact reproduction of the reported similarity numbers
  (garlic-onion 0.6134, garlic-chocolate cake 0.3316), confirming
  determinism and that the reported output was real, not fabricated.
- Dispatch 2 (build_ingredient_embeddings() batch function) - failed twice
  in a row with the same "Ollama API stream ended without a final
  response" signature seen earlier this session, even though this was a
  genuinely narrow, single-file task. Per the stated retry-then-intervene
  rule (2 failures on the same specific narrow step), built this one
  directly: a resumable batch embedder (commits every 50, skips
  already-embedded ingredients, so an interruption never loses progress).

Scoped to ingredients with total_count >= 3 (8,568 of 71,882 unique
ingredient strings) - the long tail below that is mostly raw-import noise
(e.g. "2 tablespoons fresh oregano leaves" as a literal ingredient string),
not reusable vocabulary.

**Verified for real**: smoke-tested with a restrictive threshold first (58
ingredients) before committing to the full run, confirmed real 768-
dimensional vectors were actually stored (not empty/malformed) via direct
SQL, then ran the full batch (8,568 real Ollama network calls). Final row
count independently verified via SQL: exactly 8,568, exactly matching the
expected qualifying-ingredient count.

## 2026-07-08 (final) - Phase 3: Embedding integration complete - DONE

Final piece: wired embedding similarity into suggest_companions(). Track
record of the narrow-dispatch approach across this whole phase, for
honesty about how well "manage OpenClaw more" actually worked in practice:

- get_embedding()/cosine_similarity(): succeeded on attempt 1.
- build_ingredient_embeddings(): failed twice (same known "stream ended"
  signature), built directly.
- get_ingredient_embedding_similarity(): failed twice (once burned its
  whole turn on directory-navigation confusion despite explicit
  instructions not to, once "aborted" with no explanation), built directly.
- get_embedding_boost(): failed once (same navigation-confusion pattern),
  built directly given how small and well-specified it was - not worth a
  second dispatch cycle for a ~10-line pure function.
- Final suggest_companions() integration: built directly from the start -
  this one required understanding and carefully modifying an existing,
  already-delicate scoring function (adding a new signal without breaking
  the PMI-normalized co-occurrence scoring or the cuisine/time boosts
  already in place), which is exactly the kind of task that's failed
  reliably on this model all session. Not worth dispatching.

Net: 2 of 5 steps succeeded via dispatch (both were genuinely simple,
single-function, no-existing-code-to-integrate-with tasks). The real
lesson isn't "narrow tasks always work" - it's that even single-file,
single-function *additions* to fresh code succeed more often than edits
that require understanding and integrating with existing logic. Worth
keeping in mind for how "narrow" future dispatches need to be scoped.

**A real performance bug found and fixed during verification, not just
correctness**: the first working version of the embedding boost took
9-27 seconds per suggestion request against the real corpus (a nested
similarity check per seed x candidate ingredient pair, run against every
candidate that passed the co-occurrence filter - unbounded by corpus
size). Fixed by scoring co-occurrence cheaply first, then only running the
expensive embedding boost against the top 50 candidates by that cheap
score. Reduced to 0.56s and 0.99s on the same two test cases (~15-27x
faster). Documented honest tradeoff: this cutoff can exclude a genuinely
embedding-relevant candidate if its co-occurrence-only rank was low -
verified this actually happens (2 of 5 results changed on the dessert test
case between the slow/unbounded and fast/bounded versions) rather than
assuming it away.

**Verified live through the real running server**, not just standalone:
created a real plan, added a real recipe, loaded the plan page over real
HTTP, confirmed suggestions render with the embedding-boosted results and
the full page load takes 637ms - matching the standalone benchmark,
confirming the integration works end to end, not just in isolation.

**Phase 3 (embedding-based ingredient pairing) is now complete.** Sous has:
recipe CRUD, print, real dataset (54,167 recipes, 3 sources), real
schema.org URL import, error handling/validation, meal planning with
backward-scheduling and conflict detection, and now embedding-augmented
companion suggestions - all verified live, not just claimed.

## 2026-07-09 (scheduled check-in) - Phase 10 complete: notes + cook log

No background agent running; git/DB state matched PROGRESS.md, no drift.
Dispatched net-new `cooking_log.py` (recipe_notes + cook_log tables) to
OpenClaw - succeeded structurally but had a real bug: used `conn.lastrowid`
(Connection has no such attribute) instead of the cursor's. Found via
independent re-run, fixed directly (2-line fix), re-verified with asserts -
all passed. Wired into server.py directly (integrate-with-existing: notes/
cook-log UI on the recipe page, new `/history` list page, nav link,
POST/DELETE routes) - had to order `do_DELETE`'s note/cook checks before
the generic `/api/recipe/<id>` prefix match to avoid misparsing note/cook
sub-paths as a recipe-delete. Verified live end-to-end via curl against the
real server on port 8125: add/list/delete note, log-cooked with explicit
and default (today) dates, delete cook-log entry, 404 on nonexistent
recipe, 400 on empty note_text, confirmed delete routes don't touch the
recipe itself. Real recipes.db confirmed untouched (54,167 rows) and
recipe_notes/cook_log both back to 0 rows after cleanup.

## What's Next:
0. Not a PLAN.md checklist item, but a real functional gap: wire
   `recipe_scaling.py` into server.py's recipe view (scale-factor /
   target-servings UI) - the scaling logic is built and verified but has
   no UI entry point yet. Integrate-with-existing, build directly rather
   than dispatching. Print view customization is now done (Phase 11
   complete as of 2026-07-09).
1. Nice-to-have, not urgent: `total_time` being universally 0 means the
   duration heuristic leans hard on per-step regex extraction; populating
   real prep/cook times (if a better data source is ever found) would
   improve schedule accuracy.
2. Nice-to-have: the embedding-boost top-50 cutoff has a known, verified
   tradeoff (see above) - could be revisited if suggestion quality ever
   seems to be missing good matches.
3. SPEC.md's remaining Phase 3 items (grocery list integration) - not
   started, no immediate need identified. Smart recommendations superseded
   by Phase 7/flavor profiles below.

## 2026-07-08 (roadmap update) - Phases 4-8 defined from user feature request

User asked for: import from Paprika/Tandoor/other apps, quick-add on empty
search, recipe categories, notes + a usage calendar, print-view
customization, recipe scaling, flavor profiles (what flavors are in an
ingredient/dish/cuisine, what pairs well/rarely/never, using that to help
create new recipes), and easter-egg funny recipes.

Researched import formats before scoping rather than guessing: Paprika's
`.paprikarecipes` is a well-documented, tractable format (zip of gzip-
compressed per-recipe JSON, includes a `categories` field that directly
feeds the categories work). Tandoor's export is messier - no clean public
schema, it's Django `dumpdata` fixture output - so it's deferred rather
than committed to alongside Paprika.

Resolved three real open decisions with the user before planning further:
flavor-profile data comes from LLM-tagging our own ingredient vocabulary
(not researching an external dataset first - that's noted as a future
enhancement instead); print customization starts with concrete toggles,
not a full template editor; import covers Paprika + generic schema.org
JSON for now, other apps deferred until there's an actual collection to
migrate from.

Turned this into Phases 9-13 in PLAN.md (renumbered SPEC.md's Phase
4-8 to fit PLAN.md's existing sequential phase numbering; also backfilled
Phase 8 in PLAN.md for the embedding work, which was done but never
had its own PLAN.md entry). Nothing implemented yet - this is the plan,
not the work.

## Decisions Made:
- Used Python standard library exclusively for MVP to minimize dependencies
- Selected recipes_data_food.com dataset from Hugging Face as primary data source
- Implemented SQLite for local storage without external database requirements
- Designed simple, clean web interface using basic HTML/CSS
- Created Docker packaging ready for deployment

## Dataset Import Results:
- Total dataset rows: 1,048,543
- Imported recipes: 1224
- Filtered for quality data: non-empty ingredients and instructions
- Co-occurrence pairs analyzed: 19260

## Co-occurrence Sanity Checks:
- basil + tomato: 500 recipes
- chicken + garlic: 434 recipes  
- butter + flour: 428 recipes
- sugar + vanilla: 372 recipes

## 2026-07-09 (scheduled check-in) - Phase 9: real category field, backfilled

No background agent was running; the prior Phase 8 DONE status was
independently re-verified against ground truth (git log, PLAN.md, and
PROGRESS.md all consistent) before starting new work.

Investigated the actual data before building anything, rather than trusting
PROGRESS.md's prior characterization: of the 14,718 `license='MIT'` rows,
exactly 1,223 (ids 1226-2448 - the original AkashPS11 batch, not the later
Hieu-Pham import) have a non-blank `cuisine` value, and every one of those
values is actually food.com's raw `RecipeCategory` field (e.g. "Dessert",
"Chicken Breast", "< 15 Mins", "Low Protein") - a mix of meal type,
ingredient, diet, and time tags, not real cuisine data. The other 13,495 MIT
rows (the real Hieu-Pham batch) all have blank cuisine. `license='MIT' AND
cuisine != ''` is therefore an exact, non-arbitrary predicate for the
mislabeled rows - confirmed by direct SQL, not assumed.

Built `categories.py` (new file, stdlib `sqlite3` only): a `recipe_categories`
many-to-many junction table (recipe_id, category) rather than a single new
column on `recipes` - chosen because Paprika import (later in this same
phase) supports multiple categories per recipe, so a single-value column
would need reworking almost immediately. `init_categories_table()`,
`add_category()`, `get_categories()`, `get_recipes_by_category()`,
`backfill_from_mislabeled_cuisine()`.

Dispatched this as a single net-new file to OpenClaw. First attempt claimed
success but the file didn't exist at the expected path - it wrote to
`workspace/workspace/projects/sous/categories.py` instead of
`workspace/projects/sous/categories.py` (its container cwd is already
inside `workspace/`, so the relative path doubled up). Caught this by
independently checking for the file rather than trusting the agent's report
of success - another instance of this local model's summaries not matching
reality. Since the file content itself was correct and complete, fixed by
moving it to the right path directly rather than burning a second dispatch
cycle on what was purely a path issue, not a content issue.

**Verified for real before touching production data**: ran
`backfill_from_mislabeled_cuisine()` against a scratch copy of recipes.db
first (1,223 rows backfilled, confirmed via direct SQL: 0 remaining
mislabeled MIT rows, exactly 1,223 recipe_categories rows, exactly 1,223
distinct recipe_ids). Only then ran it for real against production
recipes.db (after taking a full file-copy backup, deleted once the real run
was independently verified). Real-run verification, all via direct SQL
against recipes.db, not the script's own printed output: 0 rows remain
with `license='MIT' AND cuisine != ''`; `recipe_categories` has exactly
1,223 rows; recipe 1226 now has cuisine='' and a real
`recipe_categories` row `(1226, 'Frozen Desserts')`; total recipe count
unchanged at 54,167; the legitimate CC-BY-NC cuisine data (39,447 rows,
real lowercase cuisine tags like "american", "italian") untouched.

**Not done in this pass, deliberately scoped out**: no UI surfacing yet
(recipe view/search don't display or filter by category) - PLAN.md's Phase
9 checklist item only asked for the field + backfill, and touching
server.py's existing routes/rendering is the "integrate-with-existing"
category of task that's failed reliably on the local model all project and
needs to be done carefully, not folded into this pass. Left as an open
follow-up, not forgotten.

## 2026-07-09 (scheduled check-in) - Phase 9: Paprika import (.paprikarecipes)

No background agent was running. Independently re-verified the prior
Phase 9 category-field entry against ground truth (git log b9adc4b matches
PROGRESS.md, PLAN.md checklist consistent) before starting - no drift
found, proceeded to the next unchecked PLAN.md item.

Built `import_paprika.py` (new file, stdlib `zipfile`/`gzip`/`json`/`re`
only): `.paprikarecipes` files are a ZIP archive of gzip-compressed
per-recipe JSON entries. `parse_time_to_minutes()` (regex-extracts
hour/min mentions from Paprika's free-text time strings like "1 hour 30
min"), `parse_paprikarecipes_file()` (unzips + gzip-decompresses + parses
each entry to a raw dict), `import_paprika_file()` (maps Paprika's fields
onto the existing `Recipe`/`RecipeDatabase` API and calls `categories.
add_category()` per category - ingredients/directions are Paprika
newline-delimited strings, split into lists to match how the rest of the
app already stores them). Deliberately dropped Paprika's `notes`, `rating`,
and `image_url` fields - no DB column exists for them yet (notes is
Phase 10's job; the other two have no home yet) - out of scope for this
pass, not an oversight.

Classified as net-new (a new file calling existing modules' public API,
not editing their internals) per this project's established dispatch
heuristic, and dispatched to OpenClaw accordingly. Built a synthetic
Paprika fixture myself first (`test_data/sample.paprikarecipes`, 2 recipes
with known exact expected values) so the dispatch had a concrete,
falsifiable verification step rather than "does it run." Succeeded on the
first attempt - code matches the intended field mapping exactly.

**Caught two real gaps in the agent's self-report, not just accepted it**:
it claimed verification was done but never showed actual SQL query output
(only a paraphrase), and it left `test_data/verify_scratch.db` undeleted
despite an explicit instruction to remove it (a full 373MB copy of the
real recipes.db, not test data - deleted it). Per standing distrust of
unverified claims in this project, re-ran the import myself from scratch
against a fresh scratch copy and queried it directly:
- `import_paprika_file()` returned ids [94846, 94847].
- Direct SQL on `recipes`: both rows present with exact expected
  prep_time/cook_time/servings/url/license (lasagna: 20/45/8/user-imported/
  https://example.com/lasagna; pancakes: 10/15/4/user-imported/
  https://example.com/pancakes).
- `Recipe.to_dict()` via `RecipeDatabase.get_recipe()`: ingredients split
  into exactly 5 items and instructions into exactly 3 steps for both
  recipes, matching the fixture exactly.
- Direct SQL on `recipe_categories`: exactly 4 rows - Dinner/Italian/Pasta
  for the lasagna id, Breakfast for the pancakes id.
- Confirmed the real `recipes.db` was never touched: still exactly 54,167
  rows before and after.
- Cleaned up the scratch DB copy; `test_data/sample.paprikarecipes`
  fixture kept as a permanent regression-test asset.

## 2026-07-09 (scheduled check-in) - Phase 9 complete: quick-add on empty search

No background agent was running (`docker ps --filter name=openclaw-cli-run`
empty). Git log (1384b98) and working tree (clean) matched PROGRESS.md/
PLAN.md with no drift, so proceeded to the next unchecked item.

Classified this as integrate-with-existing (editing `serve_search()`, an
existing function in `server.py`, not a new file) - per this project's
established heuristic, this category has failed reliably on the local
model all project, so built it directly (Read/Edit) rather than
dispatching.

`serve_search()`: when `db.search_recipes(query)` returns no rows, render
a quick-add form (title prefilled with the query, ingredients/instructions
as newline-delimited textareas) instead of an empty result list. Submits
via fetch to the existing `POST /api/recipe` endpoint (reusing
`handle_create_recipe` as-is, no backend changes needed) and redirects to
`/recipe/<id>` on success, matching the existing import-page's JS pattern.

**Verified live through the real running server** (port 8124, since 8000
is occupied by an unrelated `uvicorn src.api.main:app` process on this
machine): confirmed a nonsense query renders the quick-add form and a real
query ("chicken") renders normal recipe cards with no form. POSTed a real
quick-add recipe through the actual API call the JS makes (not simulated) -
got back a real id (94846), confirmed via direct SQL that title/ingredients/
instructions persisted exactly as submitted, confirmed re-searching that
same query now returns it as a real hit instead of the quick-add form.
Deleted the test recipe via the real DELETE endpoint and confirmed via
direct SQL that recipes.db is back to exactly 54,167 rows.

**Phase 9 (Import & Organization) is now fully complete**: category field
+ backfill, Paprika import, generic bulk JSON import, and quick-add on
empty search.

## Embedding Implementation (2026-07-08)
Created embeddings.py with two functions:
- get_embedding(text: str, model: str = 'nomic-embed-text') -> list[float]
- cosine_similarity(a: list[float], b: list[float]) -> float

Tested the implementation with:
- garlic embedding length: 768
- cosine similarity between garlic and onion: 0.613387175539483
- cosine similarity between garlic and chocolate cake: 0.33158922334353996
