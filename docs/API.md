# Sous Public API (v1)

A versioned, API-key-authenticated, CORS-enabled JSON API covering the full capability set of the Sous web app: recipes, search/discovery, meal planning with backward scheduling, cooking history, preferences, and the LLM-grounded adaptation/invention features.

This is separate from the plain `/api/*` endpoints used internally by Sous's own web pages (those remain unauthenticated, same-origin only, and are not a stable contract). Everything under `/api/v1/*` is the supported public surface.

## Authentication

Every endpoint except `GET /api/v1/health` requires an API key.

Keys are created server-side via a CLI script - there is no in-app account system, so there's no authenticated session to gate a "create key" endpoint behind:

```bash
python3 manage_api_keys.py create "my integration"
# Created key #1 ("my integration"):
# sous_ovp3CP6ZBkocRF5A_wEKVF87C6fFsuhoGVJXrPdEiYs
#
# This key is shown once and not recoverable - store it now.

python3 manage_api_keys.py list
python3 manage_api_keys.py revoke <id>
```

Send the key as a bearer token (preferred) or via `X-API-Key`:

```
Authorization: Bearer sous_ovp3CP6ZBkocRF5A_wEKVF87C6fFsuhoGVJXrPdEiYs
```
```
X-API-Key: sous_ovp3CP6ZBkocRF5A_wEKVF87C6fFsuhoGVJXrPdEiYs
```

Keys are stored as SHA-256 hashes, never in plaintext. A missing or invalid key returns `401`:

```json
{"success": false, "error": "Missing or invalid API key. Send it as \"Authorization: Bearer <key>\" or \"X-API-Key: <key>\"."}
```

## Conventions

- Base path: `/api/v1/`
- All responses are JSON with a `"success": true|false` field.
- `4xx`/`5xx` status codes are used for real failures (bad input, not found, server error); a handful of "this feature has nothing to do right now" cases (e.g. adapting a recipe with no preferences set) return `200` with `"success": false` and an explanatory `error`, since they aren't errors so much as "nothing to return."
- List endpoints that paginate use `?page=` (1-indexed, default 1) and `?limit=` (default 24, capped at 100).
- CORS is wide open (`Access-Control-Allow-Origin: *`) - this API is meant to be called from other tools/devices, not just same-origin browser code. `OPTIONS` preflight requests are handled for every `/api/v1/*` path.
- Timestamps are ISO 8601.

## Recipes

### `GET /api/v1/recipes`

List or search recipes.

| Param | Description |
|---|---|
| `q` | Keyword search (SQLite FTS5 + BM25 ranking, falls back to substring match). Omit to list all recipes. |
| `page` | 1-indexed page number. |
| `limit` | Page size, max 100. |

```bash
curl -H "Authorization: Bearer $KEY" "https://host/api/v1/recipes?q=chicken+soup&limit=10"
```
```json
{"success": true, "page": 1, "limit": 10, "total": 214, "recipes": [{"id": 94859, "title": "...", ...}]}
```

### `GET /api/v1/recipes/<id>`

Single recipe, including its parsed categories and structured (quantity/unit/name) ingredients. Pass `?servings=N` to get ingredients scaled to a target serving count instead of the recipe's default.

```json
{"success": true, "recipe": {"id": 1226, "title": "...", "ingredients": [...], "categories": [...], "structured_ingredients": [...]}}
```

### `POST /api/v1/recipes`

Create a recipe. Body: `title` (required), `description`, `ingredients` (array of strings), `instructions` (array of strings), `prep_time`, `cook_time`, `total_time`, `servings`, `cuisine`, `difficulty`.

Returns `201 {"success": true, "recipe_id": <id>}`.

### `PUT /api/v1/recipes/<id>`

Update a recipe. Same body shape as create; omitted fields keep their existing value.

### `DELETE /api/v1/recipes/<id>`

Delete a recipe.

### `GET /api/v1/recipes/<id>/companions`

Recipes that pair well with this one (shared, distinctively-weighted ingredients, cuisine match, complexity balance). See Technical Report No. 1 for the scoring methodology.

### `GET /api/v1/recipes/<id>/substitutions`

For any of this recipe's ingredients on the household's disliked list (see Preferences below), embedding-nearest substitute suggestions.

```json
{"success": true, "substitutions": {"blueberries": [{"ingredient": "blackberries", "similarity": 0.796}]}}
```

### `GET /api/v1/recipes/<id>/images`

Every photo on a recipe, in display order.

```json
{"success": true, "images": [{"id": 1, "src": "https://...", "position": 0}, {"id": 2, "src": "/uploads/<generated>.png", "position": 1}]}
```

### `POST /api/v1/recipes/<id>/images`

Add a photo. Body is either `{"url": "..."}` or `{"file_base64": "<base64-encoded image bytes>"}` - `url` wins if both are somehow present. Uploaded files are validated by real content sniffing (not the filename/Content-Type you send) against JPEG/PNG/GIF/WebP, 10MB max, and stored under a server-generated filename - nothing about the name you uploaded is kept. The first photo added to a recipe also becomes its thumbnail everywhere else in the app.

```bash
curl -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/dish.jpg"}' "https://host/api/v1/recipes/1226/images"
```

### `DELETE /api/v1/recipes/<id>/images/<image_id>`

Removes a photo (and its uploaded file, if it was a local upload).

### `POST /api/v1/recipes/<id>/adapt`

Rewrite this recipe's ingredients/instructions to fit the household's saved preferences via the local LLM. Returns `{"success": false, "error": "..."}` (still `200`) if no preferences are set or the model is unreachable - not an error condition, just nothing to do.

```json
{"success": true, "adapted": {"title": "...", "ingredients": [...], "instructions": [...], "changes_summary": "..."}}
```

### `POST /api/v1/recipes/invent`

Generate a new recipe grounded in real ingredient co-occurrence data. Body: `ingredients` (array of seed ingredients, optional), `mood` (free text, optional - used to derive seeds via flavor-category extraction if `ingredients` is empty). At least one of the two must resolve to something.

```json
{"success": true, "recipe": {"title": "...", "ingredients": [...], "instructions": [...], "cuisine": "...", "grounded_in": [...]}}
```

### `POST /api/v1/recipes/import`

Import a recipe from a URL with schema.org `Recipe` markup. Body: `{"url": "..."}`.

### `POST /api/v1/recipes/import/paprika`

Bulk-import a Paprika `.paprikarecipes` export. Body: `{"file_base64": "<base64-encoded file>"}`. Returns `{"success": true, "recipe_ids": [...], "count": N}`.

### `POST /api/v1/recipes/import/bulk`

Bulk-import a schema.org-shaped recipe JSON file. Same request/response shape as the Paprika import.

## Search & discovery

Three distinct mechanisms - see Technical Report No. 2 for how they differ and their measured latency.

### `GET /api/v1/discover?have=<comma-separated ingredients>`

"What can I make with what I have." Ranks recipes by how many of the given ingredients they use.

```json
{"success": true, "matches": [{"recipe": {...}, "matched": ["onion", "garlic"], "missing": ["celery", "..."], "match_count": 2}]}
```

### `GET /api/v1/craving?q=<free text>`

Fuzzy mood/intent search ("something comforting for cold weather"). Runs the query through an LLM query planner to extract flavor categories, cuisine, and a time constraint, then ranks against those; falls back to keyword search if nothing is extracted or nothing matches.

```json
{"success": true, "interpreted_as": {"flavors": ["fatty_rich", "warm_spice"], "cuisine": null, "max_total_time_minutes": null, "keywords": [...]}, "used_fallback": false, "recipes": [...]}
```

### `GET /api/v1/pairings?ingredient=<name>`

Standalone ingredient lookup: its flavor tags, top real co-occurring ingredients, and top embedding-similar ingredients.

## Categories

### `GET /api/v1/categories`

All categories with recipe counts, most-used first.

### `GET /api/v1/categories/<name>`

Recipes in a category.

## Preferences

Sous has no multi-user concept - preferences are one household-level record.

### `GET /api/v1/preferences`
### `PUT /api/v1/preferences`

Body: `dietary_restrictions` (array of strings), `disliked_ingredients` (array of strings), `notes` (free text). Returns the saved record.

## Meal plans & scheduling

### `GET /api/v1/plans`

List all meal plans.

### `POST /api/v1/plans`

Create a plan. Body: `name` (required), `target_eat_time` (optional).

### `GET /api/v1/plans/<id>`

Plan details including its recipes.

### `DELETE /api/v1/plans/<id>`

### `POST /api/v1/plans/<id>/recipes`

Add a recipe to a plan. Body: `{"recipe_id": <id>}`.

### `DELETE /api/v1/plans/<id>/recipes/<recipe_id>`

### `GET /api/v1/plans/<id>/schedule?eat_time=<ISO 8601>`

Backward-computed cooking timeline for every recipe in the plan, working back from `eat_time`, plus any detected active-step conflicts between recipes. See Technical Report No. 3 for methodology and its measured limitations (68.1% of steps have no extractable duration and default to 5 minutes).

```bash
curl -H "Authorization: Bearer $KEY" "https://host/api/v1/plans/12/schedule?eat_time=2026-07-13T18:30:00"
```
```json
{
  "success": true,
  "eat_time": "2026-07-13T18:30:00",
  "timeline": [{"recipe_id": 94859, "recipe_title": "...", "start_time": "...", "end_time": "...", "text": "...", "duration_minutes": 5, "step_type": "active"}],
  "conflicts": [{"a": "Recipe A: step text", "b": "Recipe B: step text", "overlap_start": "...", "overlap_end": "..."}],
  "skipped_no_instructions": []
}
```

## Shopping lists

Quantities merge into one line when a recipe/plan contributes an
ingredient that shares the same canonical name and unit as an existing
unchecked line on the list; different units for the same ingredient stay
as separate lines rather than being cross-unit converted (no unit-
conversion table exists in this project).

### `GET /api/v1/shopping-lists`

All shopping lists, with item/checked counts.

### `POST /api/v1/shopping-lists`

Create a list. Body: `{"name": "..."}`.

### `GET /api/v1/shopping-lists/<id>`

List details including every item.

### `DELETE /api/v1/shopping-lists/<id>`

### `POST /api/v1/shopping-lists/<id>/items`

Add a manual item. Body: `{"name": "...", "quantity": 2, "unit": "cup"}` (`quantity`/`unit` optional).

### `PUT /api/v1/shopping-lists/<id>/items/<item_id>`

Toggle checked state. Body: `{"checked": true}`.

### `DELETE /api/v1/shopping-lists/<id>/items/<item_id>`

### `POST /api/v1/shopping-lists/<id>/from-recipe/<recipe_id>`

Add every ingredient of a recipe to the list. Optional body: `{"servings": N}` to scale quantities first. Returns `{"success": true, "items_added": N}` (lines processed, merged or new - not necessarily new rows).

```bash
curl -X POST -H "Authorization: Bearer $KEY" "https://host/api/v1/shopping-lists/3/from-recipe/1226"
```

### `POST /api/v1/shopping-lists/<id>/from-plan/<plan_id>`

Add every recipe in a meal plan to the list, one recipe at a time through the same merge logic - identical ingredients across different recipes in the plan combine into one line.

## Cooking log & notes

### `GET /api/v1/recipes/<id>/cook-log`
### `POST /api/v1/recipes/<id>/cook-log`

Log that you cooked this recipe today. Returns `{"success": true, "entry_id": <id>}`.

### `DELETE /api/v1/recipes/<id>/cook-log/<entry_id>`

### `GET /api/v1/recipes/<id>/notes`
### `POST /api/v1/recipes/<id>/notes`

Body: `{"note_text": "..."}`.

### `DELETE /api/v1/recipes/<id>/notes/<note_id>`

### `GET /api/v1/history`

Full cooking history across every recipe, most recent first.

## Pantry

What you have on hand, retained across visits rather than typed in fresh
every time. Each item's `status` is computed on every read from how long
it's been since `added_at`, relative to its ingredient's typical shelf
life (`highly_perishable` through `shelf_stable` - see
`pantry_shelf_life.py`):

- `"fresh"` - confidently still good.
- `"needs_confirmation"` - approaching or just past typical shelf life;
  surfaced separately rather than silently assumed either way.
- Items well past shelf life (>1.5x) are discarded automatically before
  they'd ever appear in a response - there's no `"expired"` status to see,
  they're just gone.

Checking a shopping-list item on (`PUT /api/v1/shopping-lists/<id>/items/<item_id>`
with `{"checked": true}`) also adds/refreshes it here automatically.

### `GET /api/v1/pantry`

```json
{"success": true, "pantry": [{"id": 3, "name": "milk", "quantity": "1 gallon", "added_at": "...", "source": "manual", "days_since_added": 9.0, "shelf_life_days": 10, "shelf_life_category": "perishable", "status": "needs_confirmation"}]}
```

### `POST /api/v1/pantry`

Add or refresh an item. Body: `{"name": "...", "quantity": "..."}` (`quantity` optional, freeform text). If an item with the same name already exists, this resets its shelf-life clock instead of creating a duplicate.

### `PUT /api/v1/pantry/<id>`

Confirm you still have it - resets the shelf-life clock. No body required.

### `DELETE /api/v1/pantry/<id>`

Remove an item, whether it's used up or you confirmed you don't actually have it.

## Health

### `GET /api/v1/health`

No API key required. Returns `{"success": true, "status": "ok"}`. Use for uptime checks.

---

## Notes on scope and honesty

This API is a thin, faithful wrapper around the same Python functions the Sous web UI itself calls - there is no separate business logic to keep in sync, and no capability exists in the API that doesn't also exist in the web app (or vice versa, as of this writing). Where an underlying feature is heuristic or has known limitations (ingredient parsing confidence, step-duration extraction, flavor tagging coverage), those limitations apply identically here; see `docs/papers/` for measured detail on each.
