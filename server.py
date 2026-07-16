#!/usr/bin/env python3
"""
Simple web server for Sous recipe manager.
"""

import http.server
import socketserver
from html import escape as escape_html
import json
import sqlite3
import urllib.parse
from pathlib import Path
import os
import base64
import tempfile

from datetime import datetime

from recipe_model import RecipeDatabase, Recipe
from import_url_recipe import import_recipe_from_url
from import_paprika import import_paprika_file
from import_bulk import import_bulk_file
from meal_planner import MealPlanDatabase
import cooking_log
from easter_egg import generate_easter_egg_recipe
from recipe_scaling import scale_recipe_to_servings_structured
import categories
from flavor_queries import get_ingredient_flavor_profile
from recipe_flavor_index import find_recipes_by_flavors
from query_planner import plan_intent_query
import preferences as prefs_module
import llm_client
import llm_credentials


def hide_builtin_recipes() -> bool:
    """Whether the household's saved preference is to only see recipes
    they've added themselves (license='user-imported'), hiding the bulk
    imported corpus. Read fresh on every call - this is one cheap SQLite
    row read, not worth caching across requests."""
    return prefs_module.get_preferences().get('hide_builtin_recipes', False)
from recipe_adaptation import suggest_substitutions, adapt_recipe_to_preferences
from recipe_invention import invent_recipe
from api_keys import verify_api_key
import shopping_list
import recipe_images
import uploads
import pantry

# Set up the database
db = RecipeDatabase()
meal_db = MealPlanDatabase()
cooking_log.init_notes_table()
cooking_log.init_cook_log_table()

def get_base_style():
    """Shared <meta viewport> + CSS used across every page. The viewport tag
    matters as much as the CSS: without it, mobile browsers render this
    page at a fixed ~980px layout width and zoom the whole result out to
    fit the screen, making everything roughly half-scale regardless of how
    good the underlying CSS is."""
    return '''
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --font-display: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
            --font-body: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            --font-mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Menlo, Consolas, monospace;

            --color-bg: #f6f1e7;
            --color-surface: #fffdf8;
            --color-text: #2b2420;
            --color-text-muted: #6d6353;
            --color-border: #e4dbc8;
            --color-primary: #4c6b4f;
            --color-primary-hover: #3d5740;
            --color-primary-text: #ffffff;
            --color-accent: #c2542b;
            --color-highlight: #9c6b1f;
            --color-tag-bg: #efe6d3;
            --color-tag-text: #5a4a2c;
            --color-warn-bg: #fbe9d6;
            --color-warn-text: #8a4a12;
            --shadow-card: 0 1px 2px rgba(43, 36, 32, 0.07), 0 1px 6px rgba(43, 36, 32, 0.06);
            --shadow-card-hover: 0 2px 4px rgba(43, 36, 32, 0.09), 0 4px 14px rgba(43, 36, 32, 0.09);
            --radius: 12px;
            --radius-sm: 8px;
            --space-1: 4px;
            --space-2: 8px;
            --space-3: 16px;
            --space-4: 24px;
            --space-5: 32px;
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --color-bg: #1c1712;
                --color-surface: #241d17;
                --color-text: #f3ede2;
                --color-text-muted: #a99c89;
                --color-border: #3a2f24;
                --color-primary: #7fb387;
                --color-primary-hover: #8fc297;
                --color-primary-text: #10190f;
                --color-accent: #e08856;
                --color-highlight: #e3b94f;
                --color-tag-bg: #2e2620;
                --color-tag-text: #d8cbb4;
                --color-warn-bg: #332417;
                --color-warn-text: #e0a262;
                --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.3), 0 1px 6px rgba(0, 0, 0, 0.25);
                --shadow-card-hover: 0 2px 4px rgba(0, 0, 0, 0.35), 0 4px 14px rgba(0, 0, 0, 0.3);
            }
        }
        * { box-sizing: border-box; }
        html { -webkit-text-size-adjust: 100%; }
        body {
            font-family: var(--font-body);
            background: var(--color-bg);
            color: var(--color-text);
            margin: 0;
            padding: var(--space-3) var(--space-3) var(--space-5);
            max-width: 720px;
            margin-inline: auto;
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
        }
        h1 {
            font-family: var(--font-display); font-weight: 600;
            font-size: 1.85em; line-height: 1.2; letter-spacing: 0.003em;
            margin: var(--space-3) 0 var(--space-2);
        }
        h2 {
            font-family: var(--font-display); font-weight: 600;
            font-size: 1.3em; margin: var(--space-4) 0 var(--space-2);
        }
        a { text-decoration: none; color: var(--color-primary); }
        a:hover { text-decoration: underline; }
        a:focus-visible, button:focus-visible, .btn:focus-visible, .print-button:focus-visible {
            outline: 2px solid var(--color-primary); outline-offset: 2px; border-radius: var(--radius-sm);
        }

        .skip-link {
            position: absolute; left: -9999px; top: 0; z-index: 100;
            background: var(--color-surface); color: var(--color-text);
            padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm);
            border: 1px solid var(--color-border);
        }
        .skip-link:focus { left: var(--space-3); top: var(--space-3); }

        /* Visually hidden but present for screen readers - for labels on
           inputs where a visible label would be redundant next to a
           self-explanatory placeholder (e.g. the search box). */
        .sr-only {
            position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
            overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
        }

        nav {
            display: flex; flex-wrap: wrap; gap: var(--space-1);
            border-bottom: 1px solid var(--color-border);
            padding-bottom: var(--space-3); margin-bottom: var(--space-3);
        }
        nav a {
            color: var(--color-text-muted); font-size: 0.92em; font-weight: 600;
            padding: var(--space-2) var(--space-2); border-radius: var(--radius-sm);
        }
        nav a:hover { background: var(--color-tag-bg); color: var(--color-text); text-decoration: none; }
        .nav-dropdown { position: relative; }
        .nav-dropdown summary {
            color: var(--color-text-muted); font-size: 0.92em; font-weight: 600;
            padding: var(--space-2) var(--space-2); border-radius: var(--radius-sm);
            cursor: pointer; list-style: none; user-select: none;
        }
        .nav-dropdown summary::-webkit-details-marker { display: none; }
        .nav-dropdown summary::after { content: ' \\25BE'; font-size: 0.8em; }
        .nav-dropdown summary:hover, .nav-dropdown[open] summary {
            background: var(--color-tag-bg); color: var(--color-text);
        }
        .nav-dropdown-menu {
            position: absolute; top: 100%; left: 0; z-index: 20; margin-top: 4px;
            display: flex; flex-direction: column; min-width: 170px;
            background: var(--color-surface); border: 1px solid var(--color-border);
            border-radius: var(--radius-sm); box-shadow: var(--shadow-card);
            padding: var(--space-1);
        }
        .nav-dropdown-menu a {
            color: var(--color-text-muted); font-size: 0.92em; font-weight: 600;
            padding: var(--space-2); border-radius: var(--radius-sm); white-space: nowrap;
        }
        .nav-dropdown-menu a:hover { background: var(--color-tag-bg); color: var(--color-text); text-decoration: none; }

        .search-box { margin-bottom: var(--space-4); display: flex; gap: var(--space-2); }
        .search-box input[type="text"] { flex: 1; margin: 0; width: auto; }

        /* Photo-forward recipe card: image fills the top, text sits below -
           replaces the old thumbnail-plus-row layout, since the photo is the
           single most important thing a recipe card can show. */
        .recipe-card {
            border: 1px solid var(--color-border); border-radius: var(--radius);
            margin: var(--space-2) 0;
            background: var(--color-surface); box-shadow: var(--shadow-card);
            overflow: hidden;
            transition: box-shadow 0.15s ease, transform 0.15s ease;
        }
        .recipe-card:hover { box-shadow: var(--shadow-card-hover); transform: translateY(-2px); }
        .recipe-card-thumb {
            display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: cover;
            background: var(--color-tag-bg);
        }
        .recipe-card-thumb-placeholder {
            display: flex; align-items: center; justify-content: center;
            width: 100%; aspect-ratio: 4 / 3;
            background: linear-gradient(135deg, var(--color-tag-bg), var(--color-border));
        }
        .recipe-card-thumb-placeholder svg { width: 34px; height: 34px; color: var(--color-text-muted); opacity: 0.55; }
        .recipe-card-body { padding: var(--space-3); min-width: 0; }
        .recipe-card-body .btn, .recipe-card-body button { margin-top: var(--space-2); }
        .recipe-title { font-family: var(--font-display); font-size: 1.1em; font-weight: 700; }
        .recipe-title a { color: var(--color-text); }
        .recipe-meta { color: var(--color-text-muted); font-size: 0.88em; margin-top: var(--space-1); }

        /* Home-page hero: one randomly-featured recipe with a real photo,
           replacing the plain "Sous Recipe Manager" h1. */
        .hero {
            display: flex; align-items: center; gap: var(--space-3);
            border-radius: var(--radius); overflow: hidden;
            margin-bottom: var(--space-4); background: var(--color-surface);
            box-shadow: var(--shadow-card); padding: var(--space-2);
        }
        .hero-image { display: block; width: 56px; height: 56px; border-radius: var(--radius-sm); object-fit: cover; flex-shrink: 0; }
        .hero-body { min-width: 0; }
        .hero-eyebrow {
            font-family: var(--font-mono); font-size: 0.72em; letter-spacing: 0.06em;
            text-transform: uppercase; color: var(--color-text-muted);
        }
        .hero-title {
            display: block; font-weight: 600; color: var(--color-text);
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }

        .recipe-header { border-bottom: 1px solid var(--color-border); padding-bottom: var(--space-3); margin-bottom: var(--space-3); }
        .recipe-header .recipe-title { font-size: 1.65em; }

        .recipe-ticket {
            display: flex; flex-wrap: wrap; align-items: center;
            font-family: var(--font-mono); font-size: 0.76em; letter-spacing: 0.03em;
            text-transform: uppercase; color: var(--color-text-muted);
            border-top: 1px dashed var(--color-border);
            border-bottom: 1px dashed var(--color-border);
            padding: var(--space-2) 0; margin: var(--space-2) 0 0;
        }
        .recipe-ticket .ticket-field {
            padding: 0 var(--space-3); border-right: 1px dashed var(--color-border);
        }
        .recipe-ticket .ticket-field:first-child { padding-left: 0; }
        .recipe-ticket .ticket-field:last-child { border-right: none; padding-right: 0; }
        .recipe-hero-wrap { position: relative; margin-bottom: var(--space-3); }
        .recipe-hero-image {
            width: 100%; max-height: 320px; object-fit: cover;
            border-radius: var(--radius); display: block;
        }
        .gallery-thumbs {
            list-style: none; display: flex; flex-wrap: wrap; gap: var(--space-2);
            padding: 0; margin: 0 0 var(--space-3);
        }
        .gallery-thumbs li { position: relative; }
        .gallery-thumbs img {
            width: 84px; height: 84px; object-fit: cover; border-radius: var(--radius-sm);
            display: block; border: 1px solid var(--color-border);
        }
        .delete-image-link {
            display: none;
            position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.65); color: #fff;
            font-size: 0.7em; padding: 1px 5px; border-radius: 3px; text-decoration: none;
        }
        .gallery-thumbs .delete-image-link { top: 2px; right: 2px; }
        body.photo-editing .delete-image-link { display: block; }
        .gallery-edit-controls { display: none; }
        .gallery.editing .gallery-edit-controls { display: block; }
        .manage-photos-toggle {
            background: none; border: none; color: var(--color-primary); text-decoration: underline;
            cursor: pointer; font-size: 0.85em; padding: 0; margin-bottom: var(--space-2);
        }
        .ingredients, .instructions, .cooking-log, .notes { margin: var(--space-4) 0; }
        .ingredients-list, .instructions-list { padding-left: 22px; }
        .ingredients-list li, .instructions-list li { margin-bottom: var(--space-1); }
        .ingredients-list li.ingredient-section-header {
            list-style: none; margin-left: -22px; font-weight: 600;
            margin-top: var(--space-3);
        }
        .ingredients-list li.ingredient-section-header:first-child { margin-top: 0; }
        .no-directions-note { color: var(--color-warn-text); background: var(--color-warn-bg); padding: var(--space-3); border-radius: var(--radius-sm); }
        .no-photo-nudge {
            color: var(--color-text-muted); background: var(--color-warn-bg);
            padding: var(--space-3); border-radius: var(--radius-sm); border: 1px dashed var(--color-border);
        }
        .no-photo-nudge a { font-weight: 600; }
.llm-test-ok { color: var(--color-primary); font-weight: 600; }
.llm-test-fail { color: var(--color-warn-text); font-weight: 600; }

        .flavor-tags { color: var(--color-text-muted); font-size: 0.85em; font-style: italic; }
        .category-tags:empty { display: none; }
        .category-tag, .flavor-tag {
            display: inline-block; background: var(--color-tag-bg); color: var(--color-tag-text);
            padding: 4px 12px; border-radius: 999px; font-size: 0.85em; margin: 2px 4px 2px 0;
        }
        a.category-tag:hover { background: var(--color-primary); color: var(--color-primary-text); text-decoration: none; }
        .llm-badge {
            display: inline-block; background: var(--color-accent);
            color: var(--color-primary-text); font-family: var(--font-mono);
            font-size: 0.65em; font-weight: 700; letter-spacing: 0.04em;
            padding: 1px 5px; border-radius: 4px; vertical-align: middle;
        }
        .category-list-item {
            display: flex; justify-content: space-between; align-items: center;
            padding: var(--space-2) var(--space-1); border-bottom: 1px solid var(--color-border);
        }

        button, .btn, .print-button, input[type="submit"] {
            background-color: var(--color-primary); color: var(--color-primary-text);
            padding: 12px 18px; border: none; border-radius: var(--radius-sm);
            font-size: 0.95em; font-weight: 600; cursor: pointer;
            min-height: 44px; display: inline-flex; align-items: center; justify-content: center;
            text-decoration: none; transition: background-color 0.15s ease;
        }
        button:hover, .btn:hover, .print-button:hover, input[type="submit"]:hover { background-color: var(--color-primary-hover); text-decoration: none; }
        .btn-accent { background-color: var(--color-accent); }
        .btn-accent:hover { filter: brightness(0.9); }
        .print-button { margin-top: var(--space-2); margin-right: var(--space-2); }

        .print-options { border: 1px solid var(--color-border); border-radius: var(--radius); padding: var(--space-3); margin-bottom: var(--space-3); background: var(--color-surface); }
        .print-options label { display: inline-block; margin-right: var(--space-3); font-weight: normal; }
        .print-options-note { color: var(--color-text-muted); font-style: italic; }

        .form-group { margin-bottom: var(--space-3); }
        label { display: block; margin-bottom: var(--space-1); font-weight: 600; font-size: 0.92em; }
        input, textarea, select {
            width: 100%; padding: 11px 12px; box-sizing: border-box;
            border: 1px solid var(--color-border); border-radius: var(--radius-sm);
            background: var(--color-surface); color: var(--color-text); font-size: 1em;
            font-family: inherit;
        }
        input:focus, textarea:focus, select:focus { outline: 2px solid var(--color-primary); outline-offset: 1px; }
        input[type="file"] { padding: var(--space-2); }
        input[type="checkbox"] { width: auto; }
        label.checkbox-label {
            display: flex; align-items: center; gap: var(--space-2);
            font-weight: 400; margin-bottom: 0;
        }

        .pagination {
            display: flex; align-items: center; justify-content: space-between; gap: var(--space-3);
            margin: var(--space-4) 0; font-size: 0.9em; color: var(--color-text-muted);
        }
        .pagination .pagination-links { display: flex; gap: var(--space-2); }
        .pagination .disabled { color: var(--color-border); }

        /* Listing pages (home/search/category/plans) opt into a wider,
           multi-column layout on desktop via body.list-page - recipe-detail
           and form pages stay at the narrower 720px reading measure, since
           a wide single column of prose/instructions is harder to read. */
        @media (min-width: 860px) {
            body.list-page { max-width: 1100px; }
            body.list-page #recipe-list, body.list-page #plan-list {
                display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
                gap: var(--space-3); align-items: start;
            }
            body.list-page .recipe-card { margin: 0; }
        }

        @media print {
            body { font-size: 12pt; max-width: none; }
            .no-print { display: none; }
        }
    </style>
    '''

def get_nav_html():
    """Shared skip-link + nav bar, used on every page except the print
    view. The skip link lets keyboard/screen-reader users jump straight to
    <main id="main-content"> instead of tabbing through the full nav on
    every page load.

    Grouped into <details>/<summary> dropdowns rather than one flat list -
    14 links wrapped to two lines with no hierarchy once Pantry/Shopping
    Lists/Add Recipe landed. <details> is natively keyboard-accessible
    (Enter/Space toggles, normal tab order) and needs no JS to function at
    all - the small script below is a pure UX nicety (only one dropdown
    open at a time, closes on outside click), not load-bearing."""
    return '''
    <a href="#main-content" class="skip-link">Skip to content</a>
    <nav>
        <a href="/">Home</a>
        <a href="/search">Search</a>
        <a href="/categories">Categories</a>
        <details class="nav-dropdown">
            <summary>Add</summary>
            <div class="nav-dropdown-menu">
                <a href="/add">Add Recipe</a>
                <a href="/import">Import</a>
            </div>
        </details>
        <details class="nav-dropdown">
            <summary>Discover</summary>
            <div class="nav-dropdown-menu">
                <a href="/discover">What Can I Make?</a>
                <a href="/craving">Craving? <span class="llm-badge" title="Uses an LLM">LLM</span></a>
                <a href="/pairings">Pairings</a>
                <a href="/invent">Invent <span class="llm-badge" title="Uses an LLM">LLM</span></a>
            </div>
        </details>
        <details class="nav-dropdown">
            <summary>Plan</summary>
            <div class="nav-dropdown-menu">
                <a href="/plans">Meal Plans</a>
                <a href="/lists">Shopping Lists</a>
                <a href="/pantry">Pantry</a>
            </div>
        </details>
        <details class="nav-dropdown">
            <summary>You</summary>
            <div class="nav-dropdown-menu">
                <a href="/preferences">Preferences</a>
                <a href="/history">Cooking History</a>
            </div>
        </details>
    </nav>
    <script>
        document.querySelectorAll('nav details').forEach(function(d) {
            d.addEventListener('toggle', function() {
                if (d.open) {
                    document.querySelectorAll('nav details').forEach(function(other) {
                        if (other !== d) other.open = false;
                    });
                }
            });
        });
        document.addEventListener('click', function(e) {
            if (!e.target.closest('nav details')) {
                document.querySelectorAll('nav details[open]').forEach(function(d) { d.open = false; });
            }
        });
    </script>
    '''

def get_add_recipe_form_html(heading, prefill_title=''):
    """Shared manual recipe-entry form (title + newline-delimited
    ingredients/instructions), posting to the existing POST /api/recipe
    endpoint - no new backend logic, just a reusable rendering of it.
    Used both standalone (/add) and embedded in the empty-search-results
    state (where prefill_title is the query that came up empty), so the
    two don't drift into two different implementations of the same form."""
    return f'''
    <div class="quick-add-box">
        <h2>{heading}</h2>
        <form id="quick-add-form">
            <div class="form-group">
                <label for="qa-title">Title:</label>
                <input type="text" id="qa-title" name="title" value="{escape_html(prefill_title)}" required>
            </div>
            <div class="form-group">
                <label for="qa-ingredients">Ingredients (one per line):</label>
                <textarea id="qa-ingredients" name="ingredients" rows="6" style="width: 100%;" placeholder="2 cups flour&#10;1 tsp salt&#10;3 eggs"></textarea>
            </div>
            <div class="form-group">
                <label for="qa-instructions">Instructions (one step per line):</label>
                <textarea id="qa-instructions" name="instructions" rows="6" style="width: 100%;" placeholder="Preheat oven to 350F.&#10;Mix dry ingredients in a large bowl."></textarea>
            </div>
            <div class="form-group">
                <label for="qa-servings">Servings:</label>
                <input type="number" id="qa-servings" name="servings" value="4" min="1" style="width: 6em;">
            </div>
            <div class="form-group">
                <label for="qa-cuisine">Cuisine (optional):</label>
                <input type="text" id="qa-cuisine" name="cuisine" placeholder="e.g. italian, mexican">
            </div>
            <button type="submit">Save Recipe</button>
        </form>
    </div>

    <script>
        document.getElementById('quick-add-form').addEventListener('submit', function(e) {{
            e.preventDefault();
            const title = document.getElementById('qa-title').value;
            const ingredients = document.getElementById('qa-ingredients').value
                .split('\\n').map(s => s.trim()).filter(s => s.length > 0);
            const instructions = document.getElementById('qa-instructions').value
                .split('\\n').map(s => s.trim()).filter(s => s.length > 0);
            const servings = parseInt(document.getElementById('qa-servings').value, 10) || 1;
            const cuisine = document.getElementById('qa-cuisine').value.trim();

            fetch('/api/recipe', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json'
                }},
                body: JSON.stringify({{title: title, ingredients: ingredients, instructions: instructions, servings: servings, cuisine: cuisine}})
            }})
            .then(response => response.json())
            .then(data => {{
                if (data.success) {{
                    window.location.href = `/recipe/${{data.recipe_id}}`;
                }} else {{
                    alert('Error: ' + data.error);
                }}
            }})
            .catch(error => {{
                alert('Error: ' + error);
            }});
        }});
    </script>
    '''

PAGE_SIZE = 24

def parse_page(query_params):
    """Parse a 1-indexed ?page= query param, defaulting to and clamping at 1."""
    raw = (query_params or {}).get('page', ['1'])[0]
    try:
        page = int(raw)
    except ValueError:
        page = 1
    return max(1, page)

def get_pagination_html(page, total, base_path, extra_query=''):
    """Prev/Next pager, used on the home and search pages. `extra_query`
    is any query string to preserve across page links (e.g. `q=...`),
    without its own leading `&`/`?`."""
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    sep = '&' if extra_query else ''

    def link(target_page, label):
        if 1 <= target_page <= total_pages and target_page != page:
            return f'<a href="{base_path}?page={target_page}{sep}{extra_query}">{label}</a>'
        return f'<span class="disabled">{label}</span>'

    return f'''
    <div class="pagination">
        <div class="pagination-links">{link(page - 1, "&larr; Prev")}</div>
        <span>Page {page} of {total_pages} &middot; {total} recipe{"s" if total != 1 else ""}</span>
        <div class="pagination-links">{link(page + 1, "Next &rarr;")}</div>
    </div>
    '''

# Simple "plate" glyph (two concentric circles) shown in place of a photo for
# the ~28% of recipes with no image_url - a flat empty box reads as broken
# once the card layout makes the image this prominent; this at least reads
# as a deliberate, designed placeholder.
_THUMB_PLACEHOLDER_SVG = (
    '<div class="recipe-card-thumb-placeholder">'
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3">'
    '<circle cx="12" cy="12" r="8"></circle><circle cx="12" cy="12" r="3"></circle>'
    '</svg></div>'
)

def recipe_thumb_html(image_url, title):
    """The placeholder glyph if image_url is empty (the common, predictable
    case - known at render time). If image_url is set but fails to actually
    load at runtime (rarer - a dead URL), falls back to removing the <img>
    entirely, matching this codebase's existing onerror convention
    elsewhere, rather than trying to swap in the placeholder via JS (not
    worth the extra complexity for a rare edge case)."""
    if not image_url:
        return _THUMB_PLACEHOLDER_SVG
    safe_title = escape_html(title)
    return (
        f'<img class="recipe-card-thumb" src="{escape_html(image_url)}" alt="{safe_title}" loading="lazy" '
        f'onerror="this.remove()">'
    )


def ingredients_list_html(structured_ingredients, display_ingredients):
    """Renders a recipe's ingredients as <li> items, with component-section
    labels ("For the Crust:") rendered as headings instead of bullets.
    structured_ingredients (RecipeDatabase.get_structured_ingredients()
    output) and display_ingredients (its scaled text, from
    scale_recipe_to_servings_structured()) are always the same length in
    the same order - the scaling call is a plain 1:1 map, never filters -
    so zipping them by position is safe."""
    items = []
    for structured, text in zip(structured_ingredients, display_ingredients):
        if structured.get('is_section_header'):
            items.append(f'<li class="ingredient-section-header">{escape_html(text)}</li>')
        else:
            items.append(f'<li>{escape_html(text)}</li>')
    return ''.join(items)

class RecipeHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for recipe management."""

    def end_headers(self):
        """Security headers on every response - overridden once here rather
        than added to each individual send_response()/end_headers() call
        site. `script-src`/`style-src` allow 'unsafe-inline' because every
        page's CSS/JS is genuinely inline (no separate static files, no
        nonce/hash infrastructure) - this isn't a rewrite of that
        architecture, just closing off *other* injection vectors (loading
        an external script/frame, MIME-sniffing, being framed). Defense in
        depth on top of the escape_html() sweep, not a replacement for it -
        inline script execution is still possible if an XSS payload ever
        slips past escaping again."""
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        super().end_headers()

    def do_GET(self):
        """Handle GET requests."""
        # Parse the URL path
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path

        if path.startswith('/api/v1/'):
            self.route_api_v1_get(path, parsed_path)
            return

        if path == '/':
            self.serve_home(urllib.parse.parse_qs(parsed_path.query))
        elif path.startswith('/recipe/') and path.endswith('/easter-egg'):
            try:
                recipe_id = int(path.split('/')[2])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid recipe id")
                return
            self.serve_easter_egg(recipe_id)
        elif path.startswith('/recipe/') and path.endswith('/edit'):
            try:
                recipe_id = int(path.split('/')[2])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid recipe id")
                return
            self.serve_edit_recipe(recipe_id)
        elif path.startswith('/recipe/'):
            try:
                recipe_id = int(path.split('/')[-1])
            except ValueError:
                self.send_error(400, "Invalid recipe id")
                return
            self.serve_recipe(recipe_id, urllib.parse.parse_qs(parsed_path.query))
        elif path.startswith('/api/search/fragment'):
            query_params = urllib.parse.parse_qs(parsed_path.query)
            query = query_params.get('q', [''])[0]
            self.serve_search_fragment(query, query_params)
        elif path.startswith('/search'):
            query_params = urllib.parse.parse_qs(parsed_path.query)
            query = query_params.get('q', [''])[0]
            self.serve_search(query, query_params)
        elif path.startswith('/pairings'):
            query_params = urllib.parse.parse_qs(parsed_path.query)
            ingredient = query_params.get('ingredient', [''])[0]
            self.serve_pairings(ingredient)
        elif path.startswith('/discover'):
            query_params = urllib.parse.parse_qs(parsed_path.query)
            have = query_params.get('have', [''])[0]
            self.serve_discover(have)
        elif path.startswith('/craving'):
            query_params = urllib.parse.parse_qs(parsed_path.query)
            q = query_params.get('q', [''])[0]
            self.serve_craving(q)
        elif path == '/preferences':
            self.serve_preferences()
        elif path == '/invent':
            self.serve_invent()
        elif path == '/api/recipes':
            self.serve_recipes_api(urllib.parse.parse_qs(parsed_path.query))
        elif path == '/import':
            self.serve_import_page()
        elif path == '/add':
            self.serve_add_recipe_page()
        elif path == '/print':
            query_params = urllib.parse.parse_qs(parsed_path.query)
            try:
                recipe_id = int(query_params.get('id', ['0'])[0])
            except ValueError:
                self.send_error(400, "Invalid recipe id")
                return
            self.serve_print_view(recipe_id, query_params)
        elif path == '/history':
            self.serve_cooking_history()
        elif path == '/categories':
            self.serve_categories_list()
        elif path.startswith('/category/'):
            category_name = urllib.parse.unquote(path[len('/category/'):])
            self.serve_category(category_name)
        elif path == '/plans':
            self.serve_plans_list()
        elif path.startswith('/api/plan/') and path.endswith('/fragment'):
            try:
                plan_id = int(path.split('/')[3])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid plan id")
                return
            self.serve_plan_fragment(plan_id)
        elif path.startswith('/plan/'):
            try:
                plan_id = int(path.split('/')[-1])
            except ValueError:
                self.send_error(400, "Invalid plan id")
                return
            self.serve_plan_detail(plan_id)
        elif path == '/lists':
            self.serve_shopping_lists()
        elif path.startswith('/list/'):
            try:
                list_id = int(path.split('/')[-1])
            except ValueError:
                self.send_error(400, "Invalid list id")
                return
            self.serve_shopping_list_detail(list_id)
        elif path.startswith('/uploads/'):
            self.serve_upload(path[len('/uploads/'):])
        elif path == '/pantry':
            self.serve_pantry()
        else:
            # No static files are actually served by this app - every page
            # is generated inline. The inherited SimpleHTTPRequestHandler
            # fallback would otherwise serve the entire working directory
            # (recipes.db, source code, .git) to any unmatched path with
            # zero access control - a real vulnerability caught in review,
            # not a hypothetical one.
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path

        if path.startswith('/api/v1/'):
            self.route_api_v1_post(path, parsed_path)
            return

        if path == '/api/recipe':
            self.handle_create_recipe()
        elif path == '/api/recipe/import':
            self.handle_import_recipe()
        elif path == '/api/recipe/import/paprika':
            self.handle_import_paprika()
        elif path == '/api/recipe/import/bulk':
            self.handle_import_bulk()
        elif path.startswith('/api/recipe/') and path.endswith('/note'):
            try:
                recipe_id = int(path.split('/')[3])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid recipe id")
                return
            self.handle_add_note(recipe_id)
        elif path.startswith('/api/recipe/') and path.endswith('/adapt'):
            try:
                recipe_id = int(path.split('/')[3])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid recipe id")
                return
            self.handle_adapt_recipe(recipe_id)
        elif path.startswith('/api/recipe/') and path.endswith('/image'):
            try:
                recipe_id = int(path.split('/')[3])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid recipe id")
                return
            self.handle_add_recipe_image(recipe_id)
        elif path.startswith('/api/recipe/') and path.endswith('/cook'):
            try:
                recipe_id = int(path.split('/')[3])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid recipe id")
                return
            self.handle_log_cooked(recipe_id)
        elif path == '/api/preferences':
            self.handle_save_preferences()
        elif path == '/api/preferences/test-llm':
            self.handle_test_llm_connection()
        elif path == '/api/recipe/invent':
            self.handle_invent_recipe()
        elif path == '/api/plan':
            self.handle_create_plan()
        elif path.startswith('/api/plan/') and path.endswith('/recipe'):
            try:
                plan_id = int(path.split('/')[3])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid plan id")
                return
            self.handle_add_recipe_to_plan(plan_id)
        elif path == '/api/shoppinglist':
            self.handle_create_shopping_list()
        elif path.startswith('/api/shoppinglist/') and path.endswith('/item'):
            try:
                list_id = int(path.split('/')[3])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid list id")
                return
            self.handle_add_shopping_list_item(list_id)
        elif '/from-recipe/' in path and path.startswith('/api/shoppinglist/'):
            parts = path.split('/')
            try:
                list_id = int(parts[3])
                recipe_id = int(parts[5])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid list or recipe id")
                return
            self.handle_add_recipe_to_shopping_list(list_id, recipe_id)
        elif '/from-plan/' in path and path.startswith('/api/shoppinglist/'):
            parts = path.split('/')
            try:
                list_id = int(parts[3])
                plan_id = int(parts[5])
            except (ValueError, IndexError):
                self.send_error(400, "Invalid list or plan id")
                return
            self.handle_add_plan_to_shopping_list(list_id, plan_id)
        elif path == '/api/pantry':
            self.handle_add_pantry_item()
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        """Handle PUT requests:
        - /api/recipe/<id> - update a recipe
        - /api/shoppinglist/<id>/item/<item_id> - toggle an item's checked state
        """
        parsed_path = urllib.parse.urlparse(self.path)

        if parsed_path.path.startswith('/api/v1/'):
            self.route_api_v1_put(parsed_path.path, parsed_path)
            return

        parts = parsed_path.path.split('/')
        if parsed_path.path.startswith('/api/shoppinglist/') and len(parts) == 6 and parts[4] == 'item':
            try:
                list_id = int(parts[3])
                item_id = int(parts[5])
            except ValueError:
                self.send_error(400, "Invalid list or item id")
                return
            self.handle_toggle_shopping_list_item(list_id, item_id)
            return

        if parsed_path.path.startswith('/api/pantry/') and len(parts) == 4:
            try:
                item_id = int(parts[3])
            except ValueError:
                self.send_error(400, "Invalid pantry item id")
                return
            self.handle_confirm_pantry_item(item_id)
            return

        if not parsed_path.path.startswith('/api/recipe/'):
            self.send_response(404)
            self.end_headers()
            return
        try:
            recipe_id = int(parsed_path.path.split('/')[-1])
        except ValueError:
            self.send_error(400, "Invalid recipe id")
            return
        self.handle_update_recipe(recipe_id)

    def do_DELETE(self):
        """Handle DELETE requests:
        - /api/recipe/<id>/note/<note_id> - delete a note
        - /api/recipe/<id>/cook/<entry_id> - delete a cook-log entry
        - /api/recipe/<id> - delete a recipe
        - /api/plan/<id> - delete a whole meal plan
        - /api/plan/<id>/recipe/<recipe_id> - remove one recipe from a plan
        """
        parsed_path = urllib.parse.urlparse(self.path)

        if parsed_path.path.startswith('/api/v1/'):
            self.route_api_v1_delete(parsed_path.path, parsed_path)
            return

        parts = parsed_path.path.split('/')

        if parsed_path.path.startswith('/api/recipe/') and len(parts) == 6 and parts[4] == 'note':
            try:
                recipe_id = int(parts[3])
                note_id = int(parts[5])
            except ValueError:
                self.send_error(400, "Invalid recipe or note id")
                return
            self.handle_delete_note(recipe_id, note_id)
        elif parsed_path.path.startswith('/api/recipe/') and len(parts) == 6 and parts[4] == 'image':
            try:
                recipe_id = int(parts[3])
                image_id = int(parts[5])
            except ValueError:
                self.send_error(400, "Invalid recipe or image id")
                return
            self.handle_delete_recipe_image(recipe_id, image_id)
        elif parsed_path.path.startswith('/api/recipe/') and len(parts) == 6 and parts[4] == 'cook':
            try:
                recipe_id = int(parts[3])
                entry_id = int(parts[5])
            except ValueError:
                self.send_error(400, "Invalid recipe or cook-log id")
                return
            self.handle_delete_cook_log_entry(recipe_id, entry_id)
        elif parsed_path.path.startswith('/api/recipe/'):
            try:
                recipe_id = int(parts[-1])
            except ValueError:
                self.send_error(400, "Invalid recipe id")
                return
            self.handle_delete_recipe(recipe_id)
        elif parsed_path.path.startswith('/api/plan/') and len(parts) == 6 and parts[4] == 'recipe':
            try:
                plan_id = int(parts[3])
                recipe_id = int(parts[5])
            except ValueError:
                self.send_error(400, "Invalid plan or recipe id")
                return
            self.handle_remove_recipe_from_plan(plan_id, recipe_id)
        elif parsed_path.path.startswith('/api/plan/') and len(parts) == 4:
            try:
                plan_id = int(parts[3])
            except ValueError:
                self.send_error(400, "Invalid plan id")
                return
            self.handle_delete_plan(plan_id)
        elif parsed_path.path.startswith('/api/shoppinglist/') and len(parts) == 6 and parts[4] == 'item':
            try:
                list_id = int(parts[3])
                item_id = int(parts[5])
            except ValueError:
                self.send_error(400, "Invalid list or item id")
                return
            self.handle_delete_shopping_list_item(list_id, item_id)
        elif parsed_path.path.startswith('/api/shoppinglist/') and len(parts) == 4:
            try:
                list_id = int(parts[3])
            except ValueError:
                self.send_error(400, "Invalid list id")
                return
            self.handle_delete_shopping_list(list_id)
        elif parsed_path.path.startswith('/api/pantry/') and len(parts) == 4:
            try:
                item_id = int(parts[3])
            except ValueError:
                self.send_error(400, "Invalid pantry item id")
                return
            self.handle_delete_pantry_item(item_id)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        """CORS preflight for the public API. The rest of the app is
        same-origin browser HTML/fetch and never needs this; only /api/v1/*
        is meant to be called cross-origin by external clients."""
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path.startswith('/api/v1/'):
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    # ==================================================================
    # Public API (/api/v1/*) - versioned, documented, API-key
    # authenticated, CORS-enabled. See docs/API.md. Kept entirely
    # separate from the unauthenticated /api/* endpoints above, which are
    # same-origin implementation details of this app's own HTML/JS pages
    # and must keep working unauthenticated for the browser UI.
    # ==================================================================

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key')

    def _send_api_json(self, status, payload):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def _require_api_key(self):
        """Checks Authorization: Bearer <key> (preferred) or X-API-Key for a
        valid, non-revoked key. Sends a 401 JSON error and returns None if
        missing/invalid; otherwise returns {'id', 'label'} for the matched
        key. /api/v1/health is intentionally the only route that skips this."""
        auth_header = self.headers.get('Authorization', '')
        raw_key = ''
        if auth_header.lower().startswith('bearer '):
            raw_key = auth_header[7:].strip()
        if not raw_key:
            raw_key = self.headers.get('X-API-Key', '').strip()

        key_info = verify_api_key(raw_key) if raw_key else None
        if key_info is None:
            self._send_api_json(401, {
                'success': False,
                'error': 'Missing or invalid API key. Send it as "Authorization: Bearer <key>" or "X-API-Key: <key>".',
            })
            return None
        return key_info

    def _api_query_params(self, parsed_path):
        return urllib.parse.parse_qs(parsed_path.query)

    def _api_read_json_body(self):
        """Like _read_json_body, but replies with the API's JSON error
        envelope (with CORS headers) on failure instead of the internal
        endpoints' plain _respond_json."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                return {}
            post_data = self.rfile.read(content_length)
            return json.loads(post_data.decode('utf-8'))
        except (ValueError, json.JSONDecodeError) as e:
            self._send_api_json(400, {'success': False, 'error': f'Invalid request body: {e}'})
            return None

    def route_api_v1_get(self, path, parsed_path):
        if path == '/api/v1/health':
            self._send_api_json(200, {'success': True, 'status': 'ok'})
            return
        if self._require_api_key() is None:
            return

        parts = path.split('/')  # ['', 'api', 'v1', <resource>, <id?>, <sub?>, <subid?>]
        qp = self._api_query_params(parsed_path)

        if path == '/api/v1/recipes':
            self._api_list_recipes(qp)
        elif len(parts) == 5 and parts[3] == 'recipes':
            self._api_get_recipe(parts[4], qp)
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'companions':
            self._api_get_companions(parts[4])
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'substitutions':
            self._api_get_substitutions(parts[4])
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'notes':
            self._api_get_notes(parts[4])
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'cook-log':
            self._api_get_cook_log(parts[4])
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'images':
            self._api_get_recipe_images(parts[4])
        elif path == '/api/v1/discover':
            self._api_discover(qp)
        elif path == '/api/v1/craving':
            self._api_craving(qp)
        elif path == '/api/v1/pairings':
            self._api_pairings(qp)
        elif path == '/api/v1/categories':
            self._api_list_categories()
        elif len(parts) == 5 and parts[3] == 'categories':
            self._api_get_category(urllib.parse.unquote(parts[4]))
        elif path == '/api/v1/preferences':
            self._api_get_preferences()
        elif path == '/api/v1/plans':
            self._api_list_plans()
        elif len(parts) == 5 and parts[3] == 'plans':
            self._api_get_plan(parts[4])
        elif len(parts) == 6 and parts[3] == 'plans' and parts[5] == 'schedule':
            self._api_get_plan_schedule(parts[4], qp)
        elif path == '/api/v1/history':
            self._api_get_history()
        elif path == '/api/v1/shopping-lists':
            self._api_list_shopping_lists()
        elif len(parts) == 5 and parts[3] == 'shopping-lists':
            self._api_get_shopping_list(parts[4])
        elif path == '/api/v1/pantry':
            self._api_get_pantry()
        else:
            self._send_api_json(404, {'success': False, 'error': f'No such endpoint: GET {path}'})

    def route_api_v1_post(self, path, parsed_path):
        if self._require_api_key() is None:
            return

        parts = path.split('/')

        if path == '/api/v1/recipes':
            self._api_create_recipe()
        elif len(parts) == 5 and parts[3] == 'recipes' and parts[4] == 'invent':
            self._api_invent_recipe()
        elif len(parts) == 5 and parts[3] == 'recipes' and parts[4] == 'import':
            self._api_import_recipe()
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[4] == 'import' and parts[5] == 'paprika':
            self._api_import_paprika()
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[4] == 'import' and parts[5] == 'bulk':
            self._api_import_bulk()
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'adapt':
            self._api_adapt_recipe(parts[4])
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'notes':
            self._api_add_note(parts[4])
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'cook-log':
            self._api_log_cooked(parts[4])
        elif len(parts) == 6 and parts[3] == 'recipes' and parts[5] == 'images':
            self._api_add_recipe_image(parts[4])
        elif path == '/api/v1/plans':
            self._api_create_plan()
        elif len(parts) == 6 and parts[3] == 'plans' and parts[5] == 'recipes':
            self._api_add_recipe_to_plan(parts[4])
        elif path == '/api/v1/shopping-lists':
            self._api_create_shopping_list()
        elif len(parts) == 6 and parts[3] == 'shopping-lists' and parts[5] == 'items':
            self._api_add_shopping_list_item(parts[4])
        elif len(parts) == 7 and parts[3] == 'shopping-lists' and parts[5] == 'from-recipe':
            self._api_add_recipe_to_shopping_list(parts[4], parts[6])
        elif len(parts) == 7 and parts[3] == 'shopping-lists' and parts[5] == 'from-plan':
            self._api_add_plan_to_shopping_list(parts[4], parts[6])
        elif path == '/api/v1/pantry':
            self._api_add_pantry_item()
        else:
            self._send_api_json(404, {'success': False, 'error': f'No such endpoint: POST {path}'})

    def route_api_v1_put(self, path, parsed_path):
        if self._require_api_key() is None:
            return

        parts = path.split('/')

        if len(parts) == 5 and parts[3] == 'recipes':
            self._api_update_recipe(parts[4])
        elif path == '/api/v1/preferences':
            self._api_update_preferences()
        elif len(parts) == 7 and parts[3] == 'shopping-lists' and parts[5] == 'items':
            self._api_toggle_shopping_list_item(parts[4], parts[6])
        elif len(parts) == 5 and parts[3] == 'pantry':
            self._api_confirm_pantry_item(parts[4])
        else:
            self._send_api_json(404, {'success': False, 'error': f'No such endpoint: PUT {path}'})

    def route_api_v1_delete(self, path, parsed_path):
        if self._require_api_key() is None:
            return

        parts = path.split('/')

        if len(parts) == 5 and parts[3] == 'recipes':
            self._api_delete_recipe(parts[4])
        elif len(parts) == 7 and parts[3] == 'recipes' and parts[5] == 'notes':
            self._api_delete_note(parts[4], parts[6])
        elif len(parts) == 7 and parts[3] == 'recipes' and parts[5] == 'cook-log':
            self._api_delete_cook_log_entry(parts[4], parts[6])
        elif len(parts) == 7 and parts[3] == 'recipes' and parts[5] == 'images':
            self._api_delete_recipe_image(parts[4], parts[6])
        elif len(parts) == 5 and parts[3] == 'plans':
            self._api_delete_plan(parts[4])
        elif len(parts) == 7 and parts[3] == 'plans' and parts[5] == 'recipes':
            self._api_remove_recipe_from_plan(parts[4], parts[6])
        elif len(parts) == 5 and parts[3] == 'shopping-lists':
            self._api_delete_shopping_list(parts[4])
        elif len(parts) == 7 and parts[3] == 'shopping-lists' and parts[5] == 'items':
            self._api_delete_shopping_list_item(parts[4], parts[6])
        elif len(parts) == 5 and parts[3] == 'pantry':
            self._api_delete_pantry_item(parts[4])
        else:
            self._send_api_json(404, {'success': False, 'error': f'No such endpoint: DELETE {path}'})

    # ---- v1 handler implementations ----

    def _parse_int_or_400(self, raw, label='id'):
        try:
            return int(raw)
        except ValueError:
            self._send_api_json(400, {'success': False, 'error': f'Invalid {label}: {raw!r}'})
            return None

    def _api_list_recipes(self, qp):
        page = parse_page(qp)
        try:
            limit = min(int(qp.get('limit', [str(PAGE_SIZE)])[0]), 100)
        except ValueError:
            limit = PAGE_SIZE
        limit = max(1, limit)
        q = qp.get('q', [''])[0].strip()
        hide_builtin = hide_builtin_recipes()

        if q:
            recipes = db.search_recipes(q, limit=limit, offset=(page - 1) * limit, exclude_builtin=hide_builtin)
            total = db.count_search_results(q, exclude_builtin=hide_builtin)
        else:
            recipes = db.get_all_recipes(limit=limit, offset=(page - 1) * limit, exclude_builtin=hide_builtin)
            total = db.count_recipes(exclude_builtin=hide_builtin)

        self._send_api_json(200, {
            'success': True,
            'page': page,
            'limit': limit,
            'total': total,
            'recipes': [r.to_dict() for r in recipes],
        })

    def _api_get_recipe(self, raw_id, qp):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return

        data = recipe.to_dict()
        raw_servings = qp.get('servings', [''])[0].strip()
        if raw_servings:
            try:
                target_servings = int(raw_servings)
                if target_servings <= 0:
                    raise ValueError
                structured = db.get_structured_ingredients(recipe_id)
                data['ingredients'] = scale_recipe_to_servings_structured(structured, recipe.servings, target_servings)
                data['servings'] = target_servings
                data['scaled_from_servings'] = recipe.servings
            except ValueError:
                self._send_api_json(400, {'success': False, 'error': 'servings must be a positive integer'})
                return
        data['categories'] = categories.get_categories(recipe_id)
        data['structured_ingredients'] = db.get_structured_ingredients(recipe_id)
        self._send_api_json(200, {'success': True, 'recipe': data})

    def _api_create_recipe(self):
        data = self._api_read_json_body()
        if data is None:
            return
        title = (data.get('title') or '').strip()
        if not title:
            self._send_api_json(400, {'success': False, 'error': 'title is required'})
            return
        recipe = Recipe(
            title=title,
            description=data.get('description', ''),
            ingredients=data.get('ingredients', []),
            instructions=data.get('instructions', []),
            prep_time=data.get('prep_time', 0),
            cook_time=data.get('cook_time', 0),
            total_time=data.get('total_time', 0),
            servings=data.get('servings', 1),
            cuisine=data.get('cuisine', ''),
            difficulty=data.get('difficulty', ''),
            license='user-imported',
        )
        try:
            recipe_id = db.save_recipe(recipe)
            self._send_api_json(201, {'success': True, 'recipe_id': recipe_id})
        except Exception as e:
            self._send_api_json(500, {'success': False, 'error': str(e)})

    def _api_update_recipe(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        existing = db.get_recipe(recipe_id)
        if not existing:
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        data = self._api_read_json_body()
        if data is None:
            return
        title = data.get('title', existing.title).strip()
        if not title:
            self._send_api_json(400, {'success': False, 'error': 'title is required'})
            return
        recipe = Recipe(
            id=recipe_id,
            title=title,
            description=data.get('description', existing.description),
            ingredients=data.get('ingredients', existing.ingredients),
            instructions=data.get('instructions', existing.instructions),
            prep_time=data.get('prep_time', existing.prep_time),
            cook_time=data.get('cook_time', existing.cook_time),
            total_time=data.get('total_time', existing.total_time),
            servings=data.get('servings', existing.servings),
            cuisine=data.get('cuisine', existing.cuisine),
            difficulty=data.get('difficulty', existing.difficulty),
            url=existing.url,
            created_at=existing.created_at,
            license=existing.license,
            image_url=existing.image_url,
            nutrition=existing.nutrition,
        )
        try:
            db.save_recipe(recipe)
            self._send_api_json(200, {'success': True, 'recipe_id': recipe_id})
        except Exception as e:
            self._send_api_json(500, {'success': False, 'error': str(e)})

    def _api_delete_recipe(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        try:
            deleted = db.delete_recipe(recipe_id)
            if deleted:
                self._send_api_json(200, {'success': True})
            else:
                self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
        except Exception as e:
            self._send_api_json(500, {'success': False, 'error': str(e)})

    def _api_get_companions(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        companions = meal_db.suggest_companions(recipe, db, limit=10)
        self._send_api_json(200, {'success': True, 'companions': companions})

    def _api_get_substitutions(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        subs = suggest_substitutions(recipe_id, meal_db)
        self._send_api_json(200, {'success': True, 'substitutions': subs})

    def _api_discover(self, qp):
        have = qp.get('have', [''])[0]
        have_list = [h.strip() for h in have.split(',') if h.strip()]
        if not have_list:
            self._send_api_json(400, {'success': False, 'error': 'have is required (comma-separated ingredient names)'})
            return
        matches = db.find_recipes_by_ingredients(have_list, limit=30, exclude_builtin=hide_builtin_recipes())
        self._send_api_json(200, {
            'success': True,
            'matches': [
                {'recipe': m['recipe'].to_dict(), 'matched': m['matched'], 'missing': m['missing'], 'match_count': m['match_count']}
                for m in matches
            ],
        })

    def _api_craving(self, qp):
        q = qp.get('q', [''])[0].strip()
        if not q:
            self._send_api_json(400, {'success': False, 'error': 'q is required'})
            return
        plan = plan_intent_query(q)
        matches = find_recipes_by_flavors(
            plan['flavors'], limit=30, cuisine=plan['cuisine'], max_total_time=plan['max_total_time_minutes'],
        )
        used_fallback = not matches
        if used_fallback:
            fallback_query = ' '.join(plan['keywords']) or q
            recipes = db.search_recipes(fallback_query, limit=30, exclude_builtin=hide_builtin_recipes())
        else:
            recipes = [r for r in (db.get_recipe(m['recipe_id']) for m in matches) if r]
        self._send_api_json(200, {
            'success': True,
            'interpreted_as': plan,
            'used_fallback': used_fallback,
            'recipes': [r.to_dict() for r in recipes],
        })

    def _api_pairings(self, qp):
        ingredient = qp.get('ingredient', [''])[0].strip()
        if not ingredient:
            self._send_api_json(400, {'success': False, 'error': 'ingredient is required'})
            return
        flavor_profile = get_ingredient_flavor_profile(ingredient)
        co_occurring = meal_db.top_pairs_for_ingredient(ingredient, limit=10)
        embedding_similar = meal_db.top_embedding_similar_ingredients(ingredient, limit=10)
        self._send_api_json(200, {
            'success': True,
            'ingredient': ingredient,
            'flavors': flavor_profile['flavors'],
            'co_occurring': co_occurring,
            'embedding_similar': embedding_similar,
        })

    def _api_list_categories(self):
        rows = categories.get_category_counts(exclude_builtin=hide_builtin_recipes())
        self._send_api_json(200, {'success': True, 'categories': [{'name': c, 'count': n} for c, n in rows]})

    def _api_get_category(self, name):
        recipe_ids = categories.get_recipes_by_category(name, exclude_builtin=hide_builtin_recipes())
        recipes = [db.get_recipe(rid) for rid in recipe_ids]
        self._send_api_json(200, {
            'success': True,
            'category': name,
            'recipes': [r.to_dict() for r in recipes if r],
        })

    def _api_get_preferences(self):
        self._send_api_json(200, {'success': True, 'preferences': prefs_module.get_preferences()})

    def _api_update_preferences(self):
        data = self._api_read_json_body()
        if data is None:
            return
        saved = prefs_module.save_preferences(
            dietary_restrictions=data.get('dietary_restrictions', []),
            disliked_ingredients=data.get('disliked_ingredients', []),
            notes=data.get('notes', ''),
        )
        self._send_api_json(200, {'success': True, 'preferences': saved})

    def _api_adapt_recipe(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        adapted = adapt_recipe_to_preferences(recipe)
        if adapted is None:
            self._send_api_json(200, {
                'success': False,
                'error': 'Could not adapt this recipe - set some preferences first, or the local model is unreachable.',
            })
            return
        self._send_api_json(200, {'success': True, 'adapted': adapted})

    def _api_get_notes(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        self._send_api_json(200, {'success': True, 'notes': cooking_log.get_notes(recipe_id)})

    def _api_add_note(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        data = self._api_read_json_body()
        if data is None:
            return
        note_text = (data.get('note_text') or '').strip()
        if not note_text:
            self._send_api_json(400, {'success': False, 'error': 'note_text is required'})
            return
        note_id = cooking_log.add_note(recipe_id, note_text)
        self._send_api_json(201, {'success': True, 'note_id': note_id})

    def _api_delete_note(self, raw_recipe_id, raw_note_id):
        recipe_id = self._parse_int_or_400(raw_recipe_id, 'recipe id')
        note_id = self._parse_int_or_400(raw_note_id, 'note id') if recipe_id is not None else None
        if recipe_id is None or note_id is None:
            return
        cooking_log.delete_note(note_id)
        self._send_api_json(200, {'success': True})

    def _api_get_cook_log(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        self._send_api_json(200, {'success': True, 'cook_log': cooking_log.get_cook_log(recipe_id)})

    def _api_get_recipe_images(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        self._send_api_json(200, {'success': True, 'images': recipe_images.get_images(recipe_id)})

    def _api_add_recipe_image(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        data = self._api_read_json_body()
        if data is None:
            return
        url = (data.get('url') or '').strip()
        if url:
            image_id = recipe_images.add_image_url(recipe_id, url)
            self._send_api_json(201, {'success': True, 'image_id': image_id})
            return
        file_b64 = data.get('file_base64', '')
        if not file_b64:
            self._send_api_json(400, {'success': False, 'error': 'url or file_base64 is required'})
            return
        try:
            filename = uploads.save_upload(file_b64)
        except ValueError as e:
            self._send_api_json(400, {'success': False, 'error': str(e)})
            return
        image_id = recipe_images.add_image_upload(recipe_id, filename)
        self._send_api_json(201, {'success': True, 'image_id': image_id})

    def _api_delete_recipe_image(self, raw_recipe_id, raw_image_id):
        recipe_id = self._parse_int_or_400(raw_recipe_id, 'recipe id')
        image_id = self._parse_int_or_400(raw_image_id, 'image id') if recipe_id is not None else None
        if recipe_id is None or image_id is None:
            return
        removed_filename = recipe_images.remove_image(image_id)
        if removed_filename is None:
            self._send_api_json(404, {'success': False, 'error': f'Image {image_id} not found'})
            return
        if removed_filename:
            uploads.delete_upload(removed_filename)
        self._send_api_json(200, {'success': True})

    def _api_log_cooked(self, raw_id):
        recipe_id = self._parse_int_or_400(raw_id)
        if recipe_id is None:
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        entry_id = cooking_log.log_cooked(recipe_id)
        self._send_api_json(201, {'success': True, 'entry_id': entry_id})

    def _api_delete_cook_log_entry(self, raw_recipe_id, raw_entry_id):
        recipe_id = self._parse_int_or_400(raw_recipe_id, 'recipe id')
        entry_id = self._parse_int_or_400(raw_entry_id, 'entry id') if recipe_id is not None else None
        if recipe_id is None or entry_id is None:
            return
        cooking_log.delete_cook_log_entry(entry_id)
        self._send_api_json(200, {'success': True})

    def _api_get_history(self):
        self._send_api_json(200, {'success': True, 'history': cooking_log.get_cook_history()})

    def _api_list_shopping_lists(self):
        self._send_api_json(200, {'success': True, 'shopping_lists': shopping_list.list_lists()})

    def _api_create_shopping_list(self):
        data = self._api_read_json_body()
        if data is None:
            return
        name = (data.get('name') or '').strip()
        if not name:
            self._send_api_json(400, {'success': False, 'error': 'name is required'})
            return
        list_id = shopping_list.create_list(name)
        self._send_api_json(201, {'success': True, 'list_id': list_id})

    def _api_get_shopping_list(self, raw_id):
        list_id = self._parse_int_or_400(raw_id, 'list id')
        if list_id is None:
            return
        lst = shopping_list.get_list(list_id)
        if not lst:
            self._send_api_json(404, {'success': False, 'error': f'List {list_id} not found'})
            return
        lst['items'] = shopping_list.get_items(list_id)
        self._send_api_json(200, {'success': True, 'shopping_list': lst})

    def _api_delete_shopping_list(self, raw_id):
        list_id = self._parse_int_or_400(raw_id, 'list id')
        if list_id is None:
            return
        if shopping_list.delete_list(list_id):
            self._send_api_json(200, {'success': True})
        else:
            self._send_api_json(404, {'success': False, 'error': f'List {list_id} not found'})

    def _api_add_shopping_list_item(self, raw_list_id):
        list_id = self._parse_int_or_400(raw_list_id, 'list id')
        if list_id is None:
            return
        if not shopping_list.get_list(list_id):
            self._send_api_json(404, {'success': False, 'error': f'List {list_id} not found'})
            return
        data = self._api_read_json_body()
        if data is None:
            return
        name = (data.get('name') or '').strip()
        if not name:
            self._send_api_json(400, {'success': False, 'error': 'name is required'})
            return
        item_id = shopping_list.add_manual_item(list_id, name, data.get('quantity'), data.get('unit'))
        self._send_api_json(201, {'success': True, 'item_id': item_id})

    def _api_toggle_shopping_list_item(self, raw_list_id, raw_item_id):
        list_id = self._parse_int_or_400(raw_list_id, 'list id')
        item_id = self._parse_int_or_400(raw_item_id, 'item id') if list_id is not None else None
        if list_id is None or item_id is None:
            return
        data = self._api_read_json_body()
        if data is None:
            return
        checked = bool(data.get('checked'))
        item_name = shopping_list.get_item_name(item_id) if checked else None
        changed = shopping_list.set_item_checked(item_id, checked)
        if changed and checked and item_name:
            pantry.add_or_refresh_item(item_name, source='shopping_list')
        if changed:
            self._send_api_json(200, {'success': True})
        else:
            self._send_api_json(404, {'success': False, 'error': f'Item {item_id} not found'})

    def _api_delete_shopping_list_item(self, raw_list_id, raw_item_id):
        list_id = self._parse_int_or_400(raw_list_id, 'list id')
        item_id = self._parse_int_or_400(raw_item_id, 'item id') if list_id is not None else None
        if list_id is None or item_id is None:
            return
        if shopping_list.remove_item(item_id):
            self._send_api_json(200, {'success': True})
        else:
            self._send_api_json(404, {'success': False, 'error': f'Item {item_id} not found'})

    def _api_add_recipe_to_shopping_list(self, raw_list_id, raw_recipe_id):
        list_id = self._parse_int_or_400(raw_list_id, 'list id')
        recipe_id = self._parse_int_or_400(raw_recipe_id, 'recipe id') if list_id is not None else None
        if list_id is None or recipe_id is None:
            return
        if not shopping_list.get_list(list_id):
            self._send_api_json(404, {'success': False, 'error': f'List {list_id} not found'})
            return
        if not db.get_recipe(recipe_id):
            self._send_api_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        data = self._api_read_json_body()
        if data is None:
            return
        count = shopping_list.add_recipe_to_list(list_id, recipe_id, db, servings=data.get('servings'))
        self._send_api_json(200, {'success': True, 'items_added': count})

    def _api_add_plan_to_shopping_list(self, raw_list_id, raw_plan_id):
        list_id = self._parse_int_or_400(raw_list_id, 'list id')
        plan_id = self._parse_int_or_400(raw_plan_id, 'plan id') if list_id is not None else None
        if list_id is None or plan_id is None:
            return
        if not shopping_list.get_list(list_id):
            self._send_api_json(404, {'success': False, 'error': f'List {list_id} not found'})
            return
        if not meal_db.get_plan(plan_id):
            self._send_api_json(404, {'success': False, 'error': f'Plan {plan_id} not found'})
            return
        count = shopping_list.add_plan_to_list(list_id, plan_id, meal_db, db)
        self._send_api_json(200, {'success': True, 'items_added': count})

    def _api_get_pantry(self):
        self._send_api_json(200, {'success': True, 'pantry': pantry.get_items()})

    def _api_add_pantry_item(self):
        data = self._api_read_json_body()
        if data is None:
            return
        name = (data.get('name') or '').strip()
        if not name:
            self._send_api_json(400, {'success': False, 'error': 'name is required'})
            return
        item_id = pantry.add_or_refresh_item(name, quantity=data.get('quantity'), source='manual')
        self._send_api_json(201, {'success': True, 'item_id': item_id})

    def _api_confirm_pantry_item(self, raw_id):
        item_id = self._parse_int_or_400(raw_id, 'item id')
        if item_id is None:
            return
        changed = pantry.confirm_item(item_id)
        if changed:
            self._send_api_json(200, {'success': True})
        else:
            self._send_api_json(404, {'success': False, 'error': f'Pantry item {item_id} not found'})

    def _api_delete_pantry_item(self, raw_id):
        item_id = self._parse_int_or_400(raw_id, 'item id')
        if item_id is None:
            return
        if pantry.remove_item(item_id):
            self._send_api_json(200, {'success': True})
        else:
            self._send_api_json(404, {'success': False, 'error': f'Pantry item {item_id} not found'})

    def _api_list_plans(self):
        self._send_api_json(200, {'success': True, 'plans': meal_db.list_plans()})

    def _api_create_plan(self):
        data = self._api_read_json_body()
        if data is None:
            return
        name = (data.get('name') or '').strip()
        if not name:
            self._send_api_json(400, {'success': False, 'error': 'name is required'})
            return
        plan_id = meal_db.create_plan(name, data.get('target_eat_time', ''))
        self._send_api_json(201, {'success': True, 'plan_id': plan_id})

    def _api_get_plan(self, raw_id):
        plan_id = self._parse_int_or_400(raw_id, 'plan id')
        if plan_id is None:
            return
        plan = meal_db.get_plan(plan_id)
        if not plan:
            self._send_api_json(404, {'success': False, 'error': f'Plan {plan_id} not found'})
            return
        recipe_ids = meal_db.get_plan_recipe_ids(plan_id)
        recipes = [db.get_recipe(rid) for rid in recipe_ids]
        plan['recipes'] = [r.to_dict() for r in recipes if r]
        self._send_api_json(200, {'success': True, 'plan': plan})

    def _api_delete_plan(self, raw_id):
        plan_id = self._parse_int_or_400(raw_id, 'plan id')
        if plan_id is None:
            return
        if meal_db.delete_plan(plan_id):
            self._send_api_json(200, {'success': True})
        else:
            self._send_api_json(404, {'success': False, 'error': f'Plan {plan_id} not found'})

    def _api_add_recipe_to_plan(self, raw_plan_id):
        plan_id = self._parse_int_or_400(raw_plan_id, 'plan id')
        if plan_id is None:
            return
        if not meal_db.get_plan(plan_id):
            self._send_api_json(404, {'success': False, 'error': f'Plan {plan_id} not found'})
            return
        data = self._api_read_json_body()
        if data is None:
            return
        recipe_id = data.get('recipe_id')
        if not isinstance(recipe_id, int) or not db.get_recipe(recipe_id):
            self._send_api_json(400, {'success': False, 'error': 'recipe_id must be an existing recipe id'})
            return
        item_id = meal_db.add_recipe_to_plan(plan_id, recipe_id)
        self._send_api_json(201, {'success': True, 'item_id': item_id})

    def _api_remove_recipe_from_plan(self, raw_plan_id, raw_recipe_id):
        plan_id = self._parse_int_or_400(raw_plan_id, 'plan id')
        recipe_id = self._parse_int_or_400(raw_recipe_id, 'recipe id') if plan_id is not None else None
        if plan_id is None or recipe_id is None:
            return
        if meal_db.remove_recipe_from_plan(plan_id, recipe_id):
            self._send_api_json(200, {'success': True})
        else:
            self._send_api_json(404, {'success': False, 'error': 'Plan/recipe pairing not found'})

    def _api_get_plan_schedule(self, raw_plan_id, qp):
        plan_id = self._parse_int_or_400(raw_plan_id, 'plan id')
        if plan_id is None:
            return
        plan = meal_db.get_plan(plan_id)
        if not plan:
            self._send_api_json(404, {'success': False, 'error': f'Plan {plan_id} not found'})
            return
        raw_eat_time = qp.get('eat_time', [''])[0].strip()
        if not raw_eat_time:
            self._send_api_json(400, {'success': False, 'error': 'eat_time is required (ISO 8601, e.g. 2026-07-13T18:30:00)'})
            return
        try:
            eat_time = datetime.fromisoformat(raw_eat_time)
        except ValueError:
            self._send_api_json(400, {'success': False, 'error': 'eat_time must be ISO 8601, e.g. 2026-07-13T18:30:00'})
            return
        result = meal_db.backward_schedule_plan(plan_id, db, eat_time)
        self._send_api_json(200, {
            'success': True,
            'eat_time': result['eat_time'].isoformat(),
            'timeline': [
                {**s, 'start_time': s['start_time'].isoformat(), 'end_time': s['end_time'].isoformat()}
                for s in result['timeline']
            ],
            'conflicts': [
                {**c, 'overlap_start': c['overlap_start'].isoformat(), 'overlap_end': c['overlap_end'].isoformat()}
                for c in result['conflicts']
            ],
            'skipped_no_instructions': result['skipped_no_instructions'],
        })

    def _api_import_recipe(self):
        data = self._api_read_json_body()
        if data is None:
            return
        url = (data.get('url') or '').strip()
        if not url:
            self._send_api_json(400, {'success': False, 'error': 'url is required'})
            return
        try:
            recipe_id = import_recipe_from_url(url)
            if recipe_id is None:
                self._send_api_json(200, {
                    'success': False,
                    'error': f'No recipe found at {url} (no schema.org Recipe markup, or the page could not be fetched)',
                })
                return
            self._send_api_json(201, {'success': True, 'recipe_id': recipe_id})
        except Exception as e:
            self._send_api_json(500, {'success': False, 'error': str(e)})

    def _api_import_paprika(self):
        data = self._api_read_json_body()
        if data is None:
            return
        file_b64 = data.get('file_base64', '')
        if not file_b64:
            self._send_api_json(400, {'success': False, 'error': 'file_base64 is required'})
            return
        try:
            file_bytes = base64.b64decode(file_b64)
        except Exception:
            self._send_api_json(400, {'success': False, 'error': 'file_base64 could not be decoded'})
            return
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.paprikarecipes', delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            recipe_ids = import_paprika_file(tmp_path)
            self._send_api_json(200, {'success': True, 'recipe_ids': recipe_ids, 'count': len(recipe_ids)})
        except Exception as e:
            self._send_api_json(500, {'success': False, 'error': str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _api_import_bulk(self):
        data = self._api_read_json_body()
        if data is None:
            return
        file_b64 = data.get('file_base64', '')
        if not file_b64:
            self._send_api_json(400, {'success': False, 'error': 'file_base64 is required'})
            return
        try:
            file_bytes = base64.b64decode(file_b64)
        except Exception:
            self._send_api_json(400, {'success': False, 'error': 'file_base64 could not be decoded'})
            return
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            recipe_ids = import_bulk_file(tmp_path)
            self._send_api_json(200, {'success': True, 'recipe_ids': recipe_ids, 'count': len(recipe_ids)})
        except Exception as e:
            self._send_api_json(500, {'success': False, 'error': str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _api_invent_recipe(self):
        data = self._api_read_json_body()
        if data is None:
            return
        seed_ingredients = [str(i) for i in (data.get('ingredients') or []) if str(i).strip()]
        mood = str(data.get('mood', '') or '')
        recipe = invent_recipe(seed_ingredients=seed_ingredients, mood=mood, meal_db=meal_db)
        if recipe is None:
            self._send_api_json(200, {
                'success': False,
                'error': 'Could not invent a recipe - give it at least one recognized ingredient or a mood, or the local model is unreachable.',
            })
            return
        self._send_api_json(200, {'success': True, 'recipe': recipe})

    def serve_home(self, query_params=None):
        """Serve the main home page."""
        page = parse_page(query_params)
        hide_builtin = hide_builtin_recipes()
        total = db.count_recipes(exclude_builtin=hide_builtin)
        pagination_html = get_pagination_html(page, total, '/')

        featured = db.get_recipe_of_the_day(exclude_builtin=hide_builtin)
        if featured:
            hero_html = f'''
            <div class="hero">
                <img class="hero-image" src="{escape_html(featured.image_url)}" alt="" onerror="this.remove()">
                <div class="hero-body">
                    <div class="hero-eyebrow">Today's pick</div>
                    <a href="/recipe/{featured.id}" class="hero-title">{escape_html(featured.title)}</a>
                </div>
            </div>
            '''
        else:
            hero_html = ''

        html = '''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Sous - Recipe Manager</title>
            <meta charset="UTF-8">
            ''' + get_base_style() + '''
        </head>
        <body class="list-page">
            ''' + get_nav_html() + '''
            <main id="main-content">
            <h1 class="sr-only">Sous Recipe Manager</h1>

            <div class="search-box">
                <form action="/search" method="get" style="display:flex; gap:8px; width:100%;">
                    <label for="home-search-q" class="sr-only">Search recipes</label>
                    <input type="text" id="home-search-q" name="q" placeholder="Search recipes...">
                    <button type="submit">Search</button>
                </form>
            </div>

            ''' + hero_html + '''

            <h2>Recent Recipes</h2>
            <div id="recipe-list">
                <!-- Recipe cards will be loaded here -->
            </div>
            ''' + pagination_html + '''

            <p><a href="/import">Import a new recipe</a></p>
            </main>

            <script>
                // Load recipes on page load. Built via DOM APIs (textContent,
                // not innerHTML string interpolation) so recipe titles can't
                // inject markup into the page.
                window.onload = function() {
                    fetch('/api/recipes?page=' + ''' + str(page) + ''')
                        .then(response => response.json())
                        .then(recipes => {
                            const listDiv = document.getElementById('recipe-list');
                            recipes.forEach(recipe => {
                                const card = document.createElement('div');
                                card.className = 'recipe-card';

                                if (recipe.image_url) {
                                    const img = document.createElement('img');
                                    img.className = 'recipe-card-thumb';
                                    img.src = recipe.image_url;
                                    img.alt = recipe.title;
                                    img.loading = 'lazy';
                                    img.onerror = function() { img.remove(); };
                                    card.appendChild(img);
                                } else {
                                    // Static markup, no recipe-derived data interpolated - safe to set via
                                    // innerHTML (unlike recipe text below, which always uses textContent).
                                    // Mirrors the server-rendered placeholder in recipe_thumb_html().
                                    const placeholder = document.createElement('div');
                                    placeholder.className = 'recipe-card-thumb-placeholder';
                                    placeholder.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="12" cy="12" r="8"></circle><circle cx="12" cy="12" r="3"></circle></svg>';
                                    card.appendChild(placeholder);
                                }

                                const body = document.createElement('div');
                                body.className = 'recipe-card-body';

                                const titleDiv = document.createElement('div');
                                titleDiv.className = 'recipe-title';
                                const titleLink = document.createElement('a');
                                titleLink.href = '/recipe/' + encodeURIComponent(recipe.id);
                                titleLink.textContent = recipe.title;
                                titleDiv.appendChild(titleLink);

                                const metaDiv = document.createElement('div');
                                metaDiv.className = 'recipe-meta';
                                metaDiv.textContent = [recipe.cuisine, recipe.servings ? recipe.servings + ' servings' : null]
                                    .filter(Boolean).join(' | ');

                                body.appendChild(titleDiv);
                                body.appendChild(metaDiv);
                                card.appendChild(body);
                                listDiv.appendChild(card);
                            });
                        });
                };
            </script>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def serve_recipe(self, recipe_id, query_params=None):
        """Serve a single recipe page."""
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self.send_response(404)
            self.end_headers()
            return

        recipe_categories = categories.get_categories(recipe_id)
        if recipe_categories:
            category_tags_html = ''.join(
                f'<a href="/category/{urllib.parse.quote(c)}" class="category-tag">{escape_html(c)}</a>'
                for c in recipe_categories
            )
        else:
            category_tags_html = ''

        query_params = query_params or {}
        scale_note = ''
        servings_input_value = recipe.servings
        target_servings = recipe.servings
        raw_servings = (query_params.get('servings') or [''])[0].strip()
        if raw_servings:
            try:
                target_servings = int(raw_servings)
                if target_servings <= 0:
                    raise ValueError
            except ValueError:
                scale_note = '<p class="no-directions-note">Servings must be a positive whole number.</p>'
                target_servings = recipe.servings
            else:
                servings_input_value = target_servings
                scale_note = (
                    f'<p class="recipe-meta">Scaled from {recipe.servings} to {target_servings} servings '
                    f'(quantity parsing is heuristic - always sanity-check unusual amounts).</p>'
                )
        # Always render from the structured (parsed) ingredient table, not
        # raw recipe.ingredients text - previously only the scaled path did
        # this, so the unscaled default view and this scaled view could show
        # differently-formatted amounts for the same recipe. factor=1.0 when
        # target_servings == recipe.servings still routes through the same
        # formatter, so there's exactly one rendering path, not two.
        structured_ingredients = db.get_structured_ingredients(recipe.id)
        display_ingredients = scale_recipe_to_servings_structured(structured_ingredients, recipe.servings, target_servings)

        if recipe.instructions:
            instructions_html = f'''<ol class="instructions-list">
                    {''.join(f'<li>{escape_html(instruction)}</li>' for instruction in recipe.instructions)}
                </ol>'''
        else:
            instructions_html = '<p class="no-directions-note">Ingredients only - no directions available for this recipe.</p>'

        notes = cooking_log.get_notes(recipe_id)
        if notes:
            notes_html = ''.join(
                f'<li>{escape_html(n["note_text"])} <span class="recipe-meta">({escape_html(n["created_at"][:10])})</span> '
                f'<a href="#" class="delete-note-link" data-note-id="{n["id"]}">delete</a></li>'
                for n in notes
            )
        else:
            notes_html = '<li class="recipe-meta" id="notes-empty">No notes yet.</li>'

        cook_entries = cooking_log.get_cook_log(recipe_id)
        if cook_entries:
            cook_log_html = ''.join(f'<li>{escape_html(str(c["cooked_at"]))}</li>' for c in cook_entries)
        else:
            cook_log_html = '<li class="recipe-meta" id="cook-log-empty">Not logged as cooked yet.</li>'

        # Companion suggestions, standalone here (unlike the meal-plan page's
        # version of this same call, this doesn't need a plan or an "Add to
        # plan" button - just surfacing what pairs well with this recipe on
        # its own page).
        conflicts = prefs_module.recipe_conflicts_with_preferences(recipe_id)
        if conflicts:
            substitutions = suggest_substitutions(recipe_id, meal_db)
            sub_bits = []
            for ingredient, alts in substitutions.items():
                alt_names = ', '.join(a['ingredient'] for a in alts)
                sub_bits.append(f'try {escape_html(alt_names)} instead of {escape_html(ingredient)}')
            sub_text = f' ({"; ".join(sub_bits)})' if sub_bits else ''
            conflict_html = (
                f'<p class="no-directions-note">Heads up: {escape_html(", ".join(conflicts))}{sub_text} '
                f'(<a href="/preferences">your preferences</a>).</p>'
            )
        else:
            conflict_html = ''

        adapt_html = f'''
            <div class="companions" id="adapt-section">
                <h2>Adapt This Recipe</h2>
                <p class="recipe-meta">Rewrite this recipe's ingredients and instructions to fit your saved preferences.</p>
                <button id="adapt-btn" data-recipe-id="{recipe.id}">Adapt for my preferences</button>
                <div id="adapt-result"></div>
            </div>
            <script>
                function escapeHtml(s) {{
                    const div = document.createElement('div');
                    div.textContent = s;
                    return div.innerHTML;
                }}
                document.getElementById('adapt-btn').addEventListener('click', function() {{
                    const btn = this;
                    const resultDiv = document.getElementById('adapt-result');
                    btn.disabled = true;
                    btn.textContent = 'Adapting... (this can take up to a minute)';
                    resultDiv.innerHTML = '';
                    fetch(`/api/recipe/${{btn.dataset.recipeId}}/adapt`, {{ method: 'POST' }})
                        .then(response => response.json())
                        .then(data => {{
                            btn.disabled = false;
                            btn.textContent = 'Adapt for my preferences';
                            if (!data.success) {{
                                resultDiv.innerHTML = `<p class="no-directions-note">${{escapeHtml(data.error)}}</p>`;
                                return;
                            }}
                            const a = data.adapted;
                            resultDiv.innerHTML = `
                                <h3>${{escapeHtml(a.title)}}</h3>
                                <p class="recipe-meta">${{escapeHtml(a.changes_summary)}}</p>
                                <ul class="ingredients-list">${{a.ingredients.map(i => `<li>${{escapeHtml(i)}}</li>`).join('')}}</ul>
                                <ol class="instructions-list">${{a.instructions.map(i => `<li>${{escapeHtml(i)}}</li>`).join('')}}</ol>
                                <button id="save-adapted-btn">Save as new recipe</button>
                            `;
                            document.getElementById('save-adapted-btn').addEventListener('click', function() {{
                                fetch('/api/recipe', {{
                                    method: 'POST',
                                    headers: {{'Content-Type': 'application/json'}},
                                    body: JSON.stringify({{title: a.title, ingredients: a.ingredients, instructions: a.instructions}})
                                }})
                                .then(response => response.json())
                                .then(saveData => {{
                                    if (saveData.success) {{
                                        window.location.href = `/recipe/${{saveData.recipe_id}}`;
                                    }} else {{
                                        alert('Error: ' + saveData.error);
                                    }}
                                }});
                            }});
                        }})
                        .catch(error => {{
                            btn.disabled = false;
                            btn.textContent = 'Adapt for my preferences';
                            resultDiv.innerHTML = `<p class="no-directions-note">Error: ${{error}}</p>`;
                        }});
                }});
            </script>
        '''

        companion_suggestions = meal_db.suggest_companions(recipe, db, limit=5)
        if companion_suggestions:
            companion_rows = ''.join(
                f'<li><a href="/recipe/{s["id"]}">{escape_html(s["title"])}</a>'
                + (f' <span class="flavor-tags">{escape_html(", ".join(s["flavor_profile"]))}</span>' if s.get("flavor_profile") else '')
                + '</li>'
                for s in companion_suggestions
            )
            companions_html = f'<ul>{companion_rows}</ul>'
        else:
            companions_html = '<p class="recipe-meta">No companion suggestions found for this recipe.</p>'

        # recipe.image_url is a denormalized cache of gallery_images[0] (see
        # recipe_images._sync_primary_image()) - shown big as the hero, so
        # the thumbnail strip below only needs the *rest* of the gallery.
        # Rendering gallery_images[0] again there was a real reported bug:
        # the same photo appearing twice on the page, once as the banner
        # and again as its own thumbnail right underneath.
        gallery_images = recipe_images.get_images(recipe.id)
        hero_image = gallery_images[0] if gallery_images else None
        thumb_images = gallery_images[1:]
        hero_delete_html = (
            f'<a href="#" class="delete-image-link hero-delete-link" data-image-id="{hero_image["id"]}">remove</a>'
            if hero_image else ''
        )

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>{escape_html(recipe.title)} - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <div class="recipe-header">
                <h1 class="recipe-title">{escape_html(recipe.title)}</h1>
                <div class="recipe-ticket">
                    {''.join(f'<span class="ticket-field">{escape_html(str(field))}</span>' for field in filter(None, [
                        recipe.cuisine or None,
                        f'{recipe.servings} serv' if recipe.servings else None,
                        f'prep {recipe.prep_time}m' if recipe.prep_time else None,
                        f'cook {recipe.cook_time}m' if recipe.cook_time else None,
                        recipe.difficulty or None,
                    ]))}
                </div>
                <p class="category-tags">{category_tags_html}</p>
                {conflict_html}
            </div>
            {f'<div class="recipe-hero-wrap">'
              f'<img class="recipe-hero-image" src="{escape_html(recipe.image_url)}" alt="{escape_html(recipe.title)}" onerror="this.remove()">'
              f'{hero_delete_html}</div>' if recipe.image_url else
              '<p class="no-photo-nudge">This recipe has no photo yet. <a href="#" onclick="togglePhotoEditing(); return false;">Add one</a></p>'}

            <div class="gallery" id="photo-gallery">
                <button type="button" class="manage-photos-toggle" id="manage-photos-toggle" onclick="togglePhotoEditing()">Manage photos</button>
                <ul class="gallery-thumbs" id="gallery-thumbs" aria-live="polite">
                    {''.join(
                        f'<li data-image-id="{img["id"]}"><img src="{escape_html(img["src"])}" alt="" loading="lazy" onerror="removeThumb(this)">'
                        f'<a href="#" class="delete-image-link" data-image-id="{img["id"]}">remove</a></li>'
                        for img in thumb_images
                    )}
                </ul>
                <div class="gallery-edit-controls">
                    <div class="form-group">
                        <label for="image-url-input">Add a photo (URL):</label>
                        <div style="display:flex; gap:8px; max-width:420px;">
                            <input type="url" id="image-url-input" placeholder="https://...">
                            <button onclick="addImageUrl()">Add</button>
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="image-file-input">Or upload a photo (JPEG/PNG/GIF/WebP, max 10MB):</label>
                        <input type="file" id="image-file-input" accept="image/*">
                    </div>
                </div>
            </div>

            <p>{escape_html(recipe.description)}</p>

            <div class="ingredients">
                <h2>Ingredients</h2>
                <form method="GET" action="/recipe/{recipe.id}" class="form-group">
                    <label for="servings-input">Scale to servings:</label>
                    <div style="display:flex; gap:8px; max-width:220px;">
                        <input type="number" id="servings-input" name="servings" min="1" value="{servings_input_value}">
                        <button type="submit">Scale</button>
                    </div>
                </form>
                {scale_note}
                <ul class="ingredients-list">
                    {ingredients_list_html(structured_ingredients, display_ingredients)}
                </ul>
                <div class="form-group">
                    <label for="shopping-list-select">Add all ingredients to a shopping list:</label>
                    <div style="display:flex; gap:8px; max-width:320px;">
                        <select id="shopping-list-select">
                            {''.join(f'<option value="{l["id"]}">{escape_html(l["name"])}</option>' for l in shopping_list.list_lists())}
                            <option value="new">+ New list&hellip;</option>
                        </select>
                        <button onclick="addRecipeToShoppingList()">Add</button>
                    </div>
                    <p id="shopping-list-add-msg" class="recipe-meta" style="display:none;">Added.</p>
                </div>
            </div>

            <div class="instructions">
                <h2>Instructions</h2>
                {instructions_html}
            </div>

            <div class="companions">
                <h2>Pairs Well With</h2>
                {companions_html}
            </div>

            {adapt_html}

            <div class="cooking-log">
                <h2>Cooking History</h2>
                <button id="mark-cooked-btn">I cooked this today</button>
                <ul id="cook-log-list" aria-live="polite">{cook_log_html}</ul>
            </div>

            <div class="notes">
                <h2>Notes</h2>
                <ul id="notes-list" aria-live="polite">{notes_html}</ul>
                <form id="add-note-form">
                    <div class="form-group">
                        <label for="note-text" class="sr-only">Add a note</label>
                        <textarea id="note-text" name="note_text" rows="2" placeholder="Add a note..."></textarea>
                    </div>
                    <button type="submit">Add Note</button>
                </form>
            </div>

            <p><a href="/print?id={recipe.id}" class="print-button" target="_blank">Print Recipe</a>
            <a href="/recipe/{recipe.id}/edit" class="print-button">Edit Recipe</a>
            <a href="/recipe/{recipe.id}/easter-egg" class="print-button btn-accent">Comedic riff</a></p>

            <p><a href="/">← Back to recipes</a></p>
            </main>

            <script>
                function togglePhotoEditing() {{
                    const gallery = document.getElementById('photo-gallery');
                    const editing = gallery.classList.toggle('editing');
                    // The hero image lives outside #photo-gallery (it's the
                    // banner above it, not part of the thumbnail strip), so
                    // its delete link can't be reached by a CSS descendant
                    // selector off .gallery.editing - toggled on <body>
                    // instead, which both are always inside.
                    document.body.classList.toggle('photo-editing', editing);
                    document.getElementById('manage-photos-toggle').textContent = editing ? 'Done' : 'Manage photos';
                    if (editing) document.getElementById('image-url-input').focus();
                }}
                function removeThumb(imgEl) {{
                    imgEl.closest('li').remove();
                }}
                function refreshGallery() {{
                    fetch('/recipe/{recipe.id}')
                        .then(response => response.text())
                        .then(html => {{
                            const parser = new DOMParser();
                            const doc = parser.parseFromString(html, 'text/html');
                            document.getElementById('gallery-thumbs').innerHTML = doc.getElementById('gallery-thumbs').innerHTML;
                            // The hero area (the big banner, or the "no photo yet"
                            // nudge when there isn't one) sits outside #gallery-thumbs,
                            // right after .recipe-header - and which of the two it is
                            // can flip either way as a result of adding/removing a
                            // photo, so the whole element is swapped, not patched.
                            const anchor = document.querySelector('.recipe-header');
                            const currentHeroArea = document.querySelector('.recipe-hero-wrap, .no-photo-nudge');
                            const newHeroArea = doc.querySelector('.recipe-hero-wrap, .no-photo-nudge');
                            if (currentHeroArea) currentHeroArea.remove();
                            if (newHeroArea) anchor.insertAdjacentElement('afterend', newHeroArea);
                        }});
                }}
                function addImageUrl() {{
                    const input = document.getElementById('image-url-input');
                    const url = input.value.trim();
                    if (!url) return;
                    fetch('/api/recipe/{recipe.id}/image', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{url: url}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (!data.success) {{ alert('Error: ' + data.error); return; }}
                        input.value = '';
                        refreshGallery();
                    }});
                }}
                document.getElementById('image-file-input').addEventListener('change', function(e) {{
                    const file = e.target.files[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = function() {{
                        const b64 = reader.result.split(',')[1];
                        fetch('/api/recipe/{recipe.id}/image', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{file_base64: b64}})
                        }})
                        .then(response => response.json())
                        .then(data => {{
                            e.target.value = '';
                            if (!data.success) {{ alert('Error: ' + data.error); return; }}
                            refreshGallery();
                        }});
                    }};
                    reader.readAsDataURL(file);
                }});
                // Delegated from document, not bound to #gallery-thumbs/.recipe-hero-wrap
                // directly - refreshGallery() below replaces both of those wholesale on
                // every add/delete, which would silently drop a listener bound to the old
                // (now-detached) node. Delegation from a node that's never replaced means
                // the freshly-inserted delete links just work, no re-binding needed.
                document.addEventListener('click', function(e) {{
                    if (e.target.classList.contains('delete-image-link')) {{
                        e.preventDefault();
                        const imageId = e.target.dataset.imageId;
                        fetch(`/api/recipe/{recipe.id}/image/${{imageId}}`, {{ method: 'DELETE' }})
                            .then(() => refreshGallery());
                    }}
                }});
                function addRecipeToShoppingList() {{
                    const select = document.getElementById('shopping-list-select');
                    const msg = document.getElementById('shopping-list-add-msg');
                    const addToList = (listId) => {{
                        fetch(`/api/shoppinglist/${{listId}}/from-recipe/{recipe.id}`, {{ method: 'POST' }})
                            .then(response => response.json())
                            .then(data => {{
                                if (!data.success) {{ alert('Error: ' + data.error); return; }}
                                msg.style.display = 'block';
                            }});
                    }};
                    if (select.value === 'new') {{
                        const name = prompt('New shopping list name:');
                        if (!name || !name.trim()) return;
                        fetch('/api/shoppinglist', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{name: name}})
                        }})
                        .then(response => response.json())
                        .then(data => {{
                            if (!data.success) {{ alert('Error: ' + data.error); return; }}
                            addToList(data.list_id);
                        }});
                    }} else {{
                        addToList(select.value);
                    }}
                }}
                function deleteNoteHandler(e) {{
                    e.preventDefault();
                    if (!confirm('Delete this note?')) return;
                    const link = e.target;
                    const noteId = link.getAttribute('data-note-id');
                    fetch(`/api/recipe/{recipe.id}/note/${{noteId}}`, {{ method: 'DELETE' }})
                        .then(response => response.json())
                        .then(data => {{
                            if (!data.success) return;
                            const list = document.getElementById('notes-list');
                            link.closest('li').remove();
                            if (list.children.length === 0) {{
                                const empty = document.createElement('li');
                                empty.className = 'recipe-meta';
                                empty.id = 'notes-empty';
                                empty.textContent = 'No notes yet.';
                                list.appendChild(empty);
                            }}
                        }});
                }}
                document.querySelectorAll('.delete-note-link').forEach(function(el) {{
                    el.addEventListener('click', deleteNoteHandler);
                }});

                document.getElementById('add-note-form').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    const textEl = document.getElementById('note-text');
                    const text = textEl.value.trim();
                    if (!text) return;
                    fetch('/api/recipe/{recipe.id}/note', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{note_text: text}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (!data.success) {{
                            alert('Error: ' + data.error);
                            return;
                        }}
                        const list = document.getElementById('notes-list');
                        const empty = document.getElementById('notes-empty');
                        if (empty) empty.remove();

                        const li = document.createElement('li');
                        li.appendChild(document.createTextNode(text + ' '));
                        const dateSpan = document.createElement('span');
                        dateSpan.className = 'recipe-meta';
                        dateSpan.textContent = '(' + new Date().toISOString().slice(0, 10) + ')';
                        li.appendChild(dateSpan);
                        li.appendChild(document.createTextNode(' '));
                        const delLink = document.createElement('a');
                        delLink.href = '#';
                        delLink.className = 'delete-note-link';
                        delLink.setAttribute('data-note-id', data.note_id);
                        delLink.textContent = 'delete';
                        delLink.addEventListener('click', deleteNoteHandler);
                        li.appendChild(delLink);
                        list.appendChild(li);
                        textEl.value = '';
                    }});
                }});

                document.getElementById('mark-cooked-btn').addEventListener('click', function() {{
                    fetch('/api/recipe/{recipe.id}/cook', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (!data.success) {{
                            alert('Error: ' + data.error);
                            return;
                        }}
                        const list = document.getElementById('cook-log-list');
                        const empty = document.getElementById('cook-log-empty');
                        if (empty) empty.remove();
                        const li = document.createElement('li');
                        li.textContent = new Date().toISOString().slice(0, 10);
                        list.insertBefore(li, list.firstChild);
                    }});
                }});
            </script>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_categories_list(self):
        """List every category with its recipe count, most-used first."""
        rows = categories.get_category_counts(exclude_builtin=hide_builtin_recipes())

        if rows:
            items_html = ''.join(
                f'<li class="category-list-item"><a href="/category/{urllib.parse.quote(cat)}">{escape_html(cat)}</a>'
                f'<span class="recipe-meta">{count} recipe{"s" if count != 1 else ""}</span></li>'
                for cat, count in rows
            )
        else:
            items_html = '<li>No categories yet.</li>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Categories - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <h1>Categories</h1>
            <ul style="list-style: none; padding: 0; max-width: 500px;">{items_html}</ul>
            </main>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_category(self, category_name):
        """List every recipe tagged with a given category."""
        recipe_ids = categories.get_recipes_by_category(category_name, exclude_builtin=hide_builtin_recipes())
        recipes = [db.get_recipe(rid) for rid in recipe_ids]
        recipes = [r for r in recipes if r is not None]

        if recipes:
            cards_html = ''.join(
                '<div class="recipe-card">'
                f'{recipe_thumb_html(r.image_url, r.title)}'
                '<div class="recipe-card-body">'
                f'<div class="recipe-title"><a href="/recipe/{r.id}">{escape_html(r.title)}</a></div>'
                f'<div class="recipe-meta">{escape_html(r.cuisine)} | {r.servings} servings</div>'
                '</div>'
                '</div>'
                for r in recipes
            )
            results_html = f'<div id="recipe-list">{cards_html}</div>'
        else:
            results_html = f'<p>No recipes found in category "{escape_html(category_name)}".</p>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>{escape_html(category_name)} - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1>Category: {escape_html(category_name)}</h1>
            <p class="recipe-meta">{len(recipes)} recipe{"s" if len(recipes) != 1 else ""}</p>
            {results_html}
            <p><a href="/categories">&larr; All categories</a></p>
            </main>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_easter_egg(self, recipe_id):
        """Generate and serve an on-demand comedic mock recipe riffing on a
        real one via the Claude CLI backend. Not persisted - regenerated
        fresh each visit, so this route is slow (several seconds, a real
        API call) by nature."""
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self.send_response(404)
            self.end_headers()
            return

        try:
            bit = generate_easter_egg_recipe(recipe)
            error_html = ''
        except RuntimeError as e:
            bit = ''
            error_html = f'<p class="no-directions-note">Could not generate one right now: {escape_html(str(e))}</p>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Easter Egg: {escape_html(recipe.title)} - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <div class="recipe-header">
                <h1 class="recipe-title">A comedic riff on {escape_html(recipe.title)}</h1>
            </div>
            {error_html}
            <pre style="white-space: pre-wrap; font-family: inherit;">{escape_html(bit)}</pre>
            <p><a href="/recipe/{recipe.id}">&larr; Back to the real recipe</a></p>
            </main>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def _search_results_and_heading(self, query, query_params=None):
        """Shared by serve_search() (full page) and serve_search_fragment()
        (live-search JSON endpoint) so the two never drift into two
        different renderings of the same results."""
        page = parse_page(query_params)
        hide_builtin = hide_builtin_recipes()
        recipes = db.search_recipes(query, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, exclude_builtin=hide_builtin)

        def recipe_card_html(recipe):
            return (
                '<div class="recipe-card">'
                f'{recipe_thumb_html(recipe.image_url, recipe.title)}'
                '<div class="recipe-card-body">'
                f'<div class="recipe-title"><a href="/recipe/{recipe.id}">{escape_html(recipe.title)}</a></div>'
                f'<div class="recipe-meta">{escape_html(recipe.cuisine)} | {recipe.servings} servings</div>'
                '</div>'
                '</div>'
            )

        recipe_cards = ''.join(recipe_card_html(recipe) for recipe in recipes)

        if recipes:
            total = db.count_search_results(query, exclude_builtin=hide_builtin)
            pagination_html = get_pagination_html(page, total, '/search', extra_query=f'q={urllib.parse.quote(query)}')
            results_html = f'<div id="recipe-list">{recipe_cards}</div>{pagination_html}'
        else:
            results_html = (
                f'<p>No recipes found for "{escape_html(query)}".</p>'
                + get_add_recipe_form_html(f'Quick-add "{escape_html(query)}"', prefill_title=query)
            )

        heading = 'Browse all recipes' if not query else f'Search Results for "{escape_html(query)}"'
        return results_html, heading

    def serve_search_fragment(self, query, query_params=None):
        """Live-search JSON endpoint (GET /api/search/fragment?q=...&page=...):
        same results/heading as the full /search page, returned as HTML
        snippets for the client to drop straight into the DOM rather than
        reconstructing markup from raw JSON - same pattern already used by
        /api/plan/<id>/fragment."""
        results_html, heading = self._search_results_and_heading(query, query_params)
        self._respond_json(200, {'results_html': results_html, 'heading': heading})

    def serve_search(self, query, query_params=None):
        """Serve search results page (?q=..., paginated via ?page=)."""
        results_html, heading = self._search_results_and_heading(query, query_params)
        page_title = 'Browse Recipes - Sous' if not query else 'Search Results - Sous'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>{page_title}</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1 id="search-heading">{heading}</h1>

            <div class="search-box">
                <form action="/search" method="get" style="display:flex; gap:8px; width:100%;">
                    <label for="search-page-q" class="sr-only">Search recipes</label>
                    <input type="text" id="search-page-q" name="q" value="{escape_html(query)}" placeholder="Search recipes..." autocomplete="off">
                    <button type="submit">Search</button>
                </form>
            </div>

            <div id="search-results">{results_html}</div>
            </main>

            <script>
                (function() {{
                    const input = document.getElementById('search-page-q');
                    const heading = document.getElementById('search-heading');
                    const resultsContainer = document.getElementById('search-results');
                    let debounceTimer = null;
                    let latestRequestId = 0;

                    // createContextualFragment (not innerHTML) so the quick-add
                    // form's own <script> in a "no results" response actually
                    // runs - innerHTML-inserted <script> tags are inert.
                    function replaceResults(html) {{
                        resultsContainer.textContent = '';
                        const range = document.createRange();
                        range.selectNode(resultsContainer);
                        resultsContainer.appendChild(range.createContextualFragment(html));
                    }}

                    input.addEventListener('input', function() {{
                        clearTimeout(debounceTimer);
                        const query = input.value;
                        debounceTimer = setTimeout(function() {{
                            const requestId = ++latestRequestId;
                            fetch('/api/search/fragment?q=' + encodeURIComponent(query))
                                .then(response => response.json())
                                .then(data => {{
                                    if (requestId !== latestRequestId) return;  // a newer keystroke already superseded this
                                    heading.textContent = data.heading;
                                    replaceResults(data.results_html);
                                    const url = query ? ('/search?q=' + encodeURIComponent(query)) : '/search';
                                    history.replaceState(null, '', url);
                                    document.title = (query ? 'Search Results' : 'Browse Recipes') + ' - Sous';
                                }})
                                .catch(() => {{}});  // network hiccup - leave the last good results showing
                        }}, 300);
                    }});
                }})();
            </script>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_pairings(self, ingredient):
        """Standalone ingredient-pairing lookup (GET /pairings?ingredient=...),
        independent of meal planning. Surfaces data that previously only
        fed into suggest_companions()'s recipe-level scoring (co-occurrence,
        embedding similarity) plus flavor tags, as a direct "what goes with
        X" query - top_pairs_for_ingredient(), top_embedding_similar_ingredients(),
        and flavor_queries.get_ingredient_flavor_profile() all already
        existed but had no route calling them before this."""
        ingredient = (ingredient or '').strip()
        results_html = ''
        if ingredient:
            normed = ingredient.lower()
            total = meal_db.get_ingredient_total(normed)
            if total == 0:
                results_html = (
                    f'<p>No pairing data for "{escape_html(ingredient)}" - it may not appear often enough '
                    f'in the corpus, or the name doesn\'t match exactly (try the singular/plural form).</p>'
                )
            else:
                profile = get_ingredient_flavor_profile(normed, db_path=db.db_path)
                flavor_html = (
                    f'<p class="flavor-tags">Flavors: {escape_html(", ".join(profile["flavors"]))}</p>'
                    if profile and profile.get('flavors') else ''
                )

                co_occurring = meal_db.top_pairs_for_ingredient(normed, limit=10)
                co_occurring_html = ''.join(
                    f'<li>{escape_html(p["ingredient"])} <span class="recipe-meta">({p["count"]} recipes)</span></li>'
                    for p in co_occurring
                ) or '<li class="recipe-meta">No co-occurrence data.</li>'

                similar = meal_db.top_embedding_similar_ingredients(normed, limit=10)
                similar_html = ''.join(
                    f'<li>{escape_html(s["ingredient"])} <span class="recipe-meta">({s["similarity"]:.2f} similarity)</span></li>'
                    for s in similar
                ) or '<li class="recipe-meta">No embedding data for this ingredient.</li>'

                results_html = f'''
                <h2>{escape_html(ingredient)}</h2>
                {flavor_html}
                <p class="recipe-meta">Appears in {total} recipes.</p>

                <h3>Commonly Used With</h3>
                <ul>{co_occurring_html}</ul>

                <h3>Similar Ingredients</h3>
                <ul>{similar_html}</ul>
                '''

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Ingredient Pairings - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1>Ingredient Pairings</h1>
            <p class="recipe-meta">See what pairs well with an ingredient, based on this collection's actual recipes.</p>
            <div class="search-box">
                <form action="/pairings" method="get" style="display:flex; gap:8px; width:100%;">
                    <label for="pairing-q" class="sr-only">Ingredient</label>
                    <input type="text" id="pairing-q" name="ingredient" value="{escape_html(ingredient)}" placeholder="e.g. garlic, basil, lemon...">
                    <button type="submit">Look up</button>
                </form>
            </div>

            {results_html}
            </main>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_discover(self, have):
        """"What can I make with what I have" (GET /discover?have=onion,
        carrot), a comma-separated ingredient list matched against
        RecipeDatabase.find_recipes_by_ingredients() - no model involved,
        this is a structured query over data already in the corpus, not a
        fuzzy/intent-based search (that's a separate, harder problem)."""
        have = (have or '').strip()
        have_list = [h.strip() for h in have.split(',') if h.strip()]

        results_html = ''
        if have_list:
            matches = db.find_recipes_by_ingredients(have_list, limit=30, exclude_builtin=hide_builtin_recipes())
            if matches:
                def match_card_html(m):
                    recipe = m['recipe']
                    missing_preview = ', '.join(m['missing'][:5])
                    if len(m['missing']) > 5:
                        missing_preview += f', +{len(m["missing"]) - 5} more'
                    missing_html = f'<div class="recipe-meta">Also needs: {escape_html(missing_preview)}</div>' if m['missing'] else ''
                    return (
                        '<div class="recipe-card">'
                        f'{recipe_thumb_html(recipe.image_url, recipe.title)}'
                        '<div class="recipe-card-body">'
                        f'<div class="recipe-title"><a href="/recipe/{recipe.id}">{escape_html(recipe.title)}</a></div>'
                        f'<div class="recipe-meta">Uses {escape_html(", ".join(m["matched"]))}</div>'
                        f'{missing_html}'
                        '</div>'
                        '</div>'
                    )
                cards = ''.join(match_card_html(m) for m in matches)
                results_html = f'<div id="recipe-list">{cards}</div>'
            else:
                results_html = f'<p>No recipes found using {escape_html(", ".join(have_list))}.</p>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>What Can I Make? - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1>What Can I Make?</h1>
            <p class="recipe-meta">List what you have on hand, find recipes that use it.</p>
            <div class="search-box">
                <form action="/discover" method="get" style="display:flex; gap:8px; width:100%;">
                    <label for="discover-q" class="sr-only">Ingredients you have</label>
                    <input type="text" id="discover-q" name="have" value="{escape_html(have)}" placeholder="e.g. onion, carrot, celery">
                    <button type="submit">Find recipes</button>
                </form>
            </div>

            {results_html}
            </main>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_craving(self, q):
        """Fuzzy-intent recipe search (GET /craving?q=...): a free-text mood/craving
        description ("something comforting for cold weather") gets translated by an LLM
        query planner into flavor-category/cuisine/time filters, then matched against the
        precomputed recipe_flavors index. Falls back to plain keyword search if the planner
        can't extract any flavors (or Ollama is unreachable) so the box never dead-ends."""
        q = (q or '').strip()

        results_html = ''
        interpreted_html = ''
        if q:
            plan = plan_intent_query(q)
            matches = find_recipes_by_flavors(
                plan['flavors'], limit=30, cuisine=plan['cuisine'],
                max_total_time=plan['max_total_time_minutes'],
            )

            used_fallback = False
            if not matches:
                used_fallback = True
                fallback_query = ' '.join(plan['keywords']) or q
                recipes = db.search_recipes(fallback_query, limit=30, exclude_builtin=hide_builtin_recipes())
            else:
                recipes = []
                for m in matches:
                    recipe = db.get_recipe(m['recipe_id'])
                    if recipe:
                        recipes.append(recipe)

            interpreted_bits = []
            if plan['flavors']:
                interpreted_bits.append(', '.join(plan['flavors']).replace('_', ' '))
            if plan['cuisine']:
                interpreted_bits.append(f"{plan['cuisine']} cuisine")
            if plan['max_total_time_minutes']:
                interpreted_bits.append(f"under {plan['max_total_time_minutes']} min")
            if interpreted_bits:
                note = f"Interpreted as: {escape_html(' · '.join(interpreted_bits))}"
                if used_fallback:
                    note += ' (no strong matches - showing keyword results instead)'
                interpreted_html = f'<p class="recipe-meta">{note}</p>'
            elif used_fallback:
                interpreted_html = '<p class="recipe-meta">Showing keyword results.</p>'

            if recipes:
                def craving_card_html(recipe):
                    return (
                        '<div class="recipe-card">'
                        f'{recipe_thumb_html(recipe.image_url, recipe.title)}'
                        '<div class="recipe-card-body">'
                        f'<div class="recipe-title"><a href="/recipe/{recipe.id}">{escape_html(recipe.title)}</a></div>'
                        f'<div class="recipe-meta">{escape_html(recipe.cuisine)} | {recipe.servings} servings</div>'
                        '</div>'
                        '</div>'
                    )
                cards = ''.join(craving_card_html(r) for r in recipes)
                results_html = f'<div id="recipe-list">{cards}</div>'
            else:
                results_html = f'<p>No recipes found for "{escape_html(q)}".</p>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Craving? - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1>Craving? <span class="llm-badge" title="Uses an LLM">LLM</span></h1>
            <p class="recipe-meta">Describe what you're in the mood for - mood, weather, occasion, whatever. Your text is sent to an LLM (see <a href="/preferences">Preferences</a> for which provider) to interpret it into flavor/cuisine/time filters.</p>
            <div class="search-box">
                <form action="/craving" method="get" style="display:flex; gap:8px; width:100%;">
                    <label for="craving-q" class="sr-only">What are you craving?</label>
                    <input type="text" id="craving-q" name="q" value="{escape_html(q)}" placeholder="e.g. something comforting for cold weather">
                    <button type="submit">Find recipes</button>
                </form>
            </div>

            {interpreted_html}
            {results_html}
            </main>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_preferences(self):
        """Household dietary restrictions / disliked ingredients / free-text rules
        (GET /preferences). Single-user app - one saved set, no per-user accounts. This is
        the grounding data for recipe adaptation and invention, not just a display page."""
        current = prefs_module.get_preferences()
        dietary_value = ', '.join(current['dietary_restrictions'])
        disliked_value = ', '.join(current['disliked_ingredients'])
        notes_value = current['notes']
        hide_builtin_checked = 'checked' if current['hide_builtin_recipes'] else ''

        active_provider, active_model = llm_client.get_active_provider_and_model()
        active_ollama_host = llm_client.get_ollama_host()
        provider_options_html = ''.join(
            f'<option value="{provider_id}" {"selected" if provider_id == active_provider else ""}>{escape_html(display_name)}</option>'
            for provider_id, display_name, _, _ in llm_client.PROVIDERS
        )
        # Per-provider "needs an API key you haven't set" flags, checked
        # client-side on provider-select change so switching shows/hides
        # the key field and warning immediately without a round trip -
        # none of this is secret (it's just booleans + env var *names*,
        # never the key values themselves), safe to inline into the page.
        key_status_json = json.dumps({
            provider_id: {
                'needs_key': env_var is not None,
                'configured': llm_client.api_key_configured(provider_id),
                'env_var': env_var,
            }
            for provider_id, _, _, env_var in llm_client.PROVIDERS
        })

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Preferences - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <h1>Preferences</h1>
            <p class="recipe-meta">These guide recipe suggestions, adaptations, and anything Sous generates for you.</p>
            <form id="preferences-form">
                <div class="form-group">
                    <label for="pref-dietary">Dietary restrictions (comma-separated):</label>
                    <input type="text" id="pref-dietary" name="dietary_restrictions" value="{escape_html(dietary_value)}" placeholder="e.g. vegetarian, gluten-free" style="width: 100%;">
                </div>
                <div class="form-group">
                    <label for="pref-disliked">Disliked ingredients (comma-separated):</label>
                    <input type="text" id="pref-disliked" name="disliked_ingredients" value="{escape_html(disliked_value)}" placeholder="e.g. cilantro, mushrooms" style="width: 100%;">
                </div>
                <div class="form-group">
                    <label for="pref-notes">Other rules/guidelines:</label>
                    <textarea id="pref-notes" name="notes" rows="6" style="width: 100%;" placeholder="e.g. keep sodium low, kids don't like spicy food, we don't eat pork">{escape_html(notes_value)}</textarea>
                </div>
                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox" id="pref-hide-builtin" name="hide_builtin_recipes" {hide_builtin_checked}>
                        Hide built-in recipes (only show recipes I've added myself)
                    </label>
                </div>

                <h2>LLM Provider</h2>
                <p class="recipe-meta">Powers flavor tagging, pantry shelf-life, craving search, recipe adaptation/invention, and the comedic riff. Takes effect immediately - no restart needed.</p>
                <div class="form-group">
                    <label for="pref-llm-provider">Provider:</label>
                    <select id="pref-llm-provider">
                        {provider_options_html}
                    </select>
                </div>
                <div class="form-group">
                    <label for="pref-llm-model">Model:</label>
                    <input type="text" id="pref-llm-model" value="{escape_html(active_model)}" placeholder="e.g. qwen3:8b">
                </div>
                <div class="form-group" id="pref-ollama-host-group" style="display:none;">
                    <label for="pref-ollama-host">Ollama host:</label>
                    <input type="text" id="pref-ollama-host" value="{escape_html(active_ollama_host)}" placeholder="http://192.168.1.x:11434">
                    <p class="recipe-meta">Where to reach Ollama - your LAN box, localhost, wherever it's running. Also configurable via OLLAMA_HOST if you'd rather not use the UI; this takes priority when set.</p>
                </div>
                <div class="form-group" id="pref-llm-key-group" style="display:none;">
                    <label for="pref-llm-api-key">API key:</label>
                    <input type="password" id="pref-llm-api-key" placeholder="" autocomplete="off">
                    <p class="recipe-meta">Stored locally on this server (a separate file, not part of the recipe database, never committed to git) - never sent anywhere except that provider's own API. Leave blank to keep whatever's already saved.</p>
                </div>
                <p id="pref-llm-key-warning" class="no-directions-note" style="display:none;"></p>
                <div class="form-group">
                    <button type="button" id="pref-llm-test-btn">Test Connection</button>
                    <p id="pref-llm-test-result" style="display:none;"></p>
                </div>

                <button type="submit">Save Preferences</button>
            </form>
            <p id="pref-saved-msg" class="recipe-meta" style="display:none;">Saved.</p>

            <script>
                const llmKeyStatus = {key_status_json};
                const llmProviderSelect = document.getElementById('pref-llm-provider');
                const llmModelInput = document.getElementById('pref-llm-model');
                const llmKeyWarning = document.getElementById('pref-llm-key-warning');
                const llmKeyGroup = document.getElementById('pref-llm-key-group');
                const llmKeyInput = document.getElementById('pref-llm-api-key');
                const ollamaHostGroup = document.getElementById('pref-ollama-host-group');
                const ollamaHostInput = document.getElementById('pref-ollama-host');
                const llmDefaultModels = {json.dumps({p[0]: p[2] for p in llm_client.PROVIDERS})};

                function updateLlmKeyUi() {{
                    const status = llmKeyStatus[llmProviderSelect.value];
                    llmKeyInput.value = '';
                    ollamaHostGroup.style.display = llmProviderSelect.value === 'ollama' ? 'block' : 'none';
                    if (status && status.needs_key) {{
                        llmKeyGroup.style.display = 'block';
                        llmKeyInput.placeholder = status.configured ? 'Already set - leave blank to keep' : 'Not set yet';
                    }} else {{
                        llmKeyGroup.style.display = 'none';
                    }}
                    if (status && status.needs_key && !status.configured) {{
                        llmKeyWarning.textContent = `${{llmProviderSelect.options[llmProviderSelect.selectedIndex].text}} needs an API key - enter it above, or set ${{status.env_var}} in the container environment.`;
                        llmKeyWarning.style.display = 'block';
                    }} else {{
                        llmKeyWarning.style.display = 'none';
                    }}
                }}
                llmProviderSelect.addEventListener('change', function() {{
                    updateLlmKeyUi();
                    // Swap in that provider's default model as a starting point,
                    // but only if the field still holds some *other* provider's
                    // default (not overwriting a real custom model the household typed in).
                    if (Object.values(llmDefaultModels).includes(llmModelInput.value)) {{
                        llmModelInput.value = llmDefaultModels[llmProviderSelect.value] || '';
                    }}
                }});
                updateLlmKeyUi();

                document.getElementById('pref-llm-test-btn').addEventListener('click', function() {{
                    const btn = this;
                    const resultEl = document.getElementById('pref-llm-test-result');
                    btn.disabled = true;
                    btn.textContent = 'Testing...';
                    resultEl.style.display = 'none';

                    fetch('/api/preferences/test-llm', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            provider: llmProviderSelect.value,
                            model: llmModelInput.value.trim(),
                            api_key: llmKeyInput.value,
                            ollama_host: ollamaHostInput.value.trim(),
                        }})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        resultEl.textContent = data.message;
                        resultEl.className = data.success ? 'llm-test-ok' : 'llm-test-fail';
                        resultEl.style.display = 'block';
                    }})
                    .catch(error => {{
                        resultEl.textContent = 'Test failed: ' + error;
                        resultEl.className = 'llm-test-fail';
                        resultEl.style.display = 'block';
                    }})
                    .finally(() => {{
                        btn.disabled = false;
                        btn.textContent = 'Test Connection';
                    }});
                }});

                document.getElementById('preferences-form').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    const dietary_restrictions = document.getElementById('pref-dietary').value
                        .split(',').map(s => s.trim()).filter(s => s.length > 0);
                    const disliked_ingredients = document.getElementById('pref-disliked').value
                        .split(',').map(s => s.trim()).filter(s => s.length > 0);
                    const notes = document.getElementById('pref-notes').value;
                    const hide_builtin_recipes = document.getElementById('pref-hide-builtin').checked;
                    const llm_provider = llmProviderSelect.value;
                    const llm_model = llmModelInput.value.trim();
                    const llm_api_key = llmKeyInput.value;  // blank = "don't change" (server-side)
                    const ollama_host = ollamaHostInput.value.trim();

                    fetch('/api/preferences', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{dietary_restrictions, disliked_ingredients, notes, hide_builtin_recipes, llm_provider, llm_model, llm_api_key, ollama_host}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            document.getElementById('pref-saved-msg').style.display = 'block';
                            if (llm_api_key && llmKeyStatus[llm_provider]) {{
                                llmKeyStatus[llm_provider].configured = true;
                                updateLlmKeyUi();
                            }}
                        }} else {{
                            alert('Error: ' + data.error);
                        }}
                    }})
                    .catch(error => alert('Error: ' + error));
                }});
            </script>
            </main>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_invent(self):
        """Grounded recipe invention (GET /invent): unlike easter_egg.py's comedic riffs on
        an existing recipe, this generates a genuinely new, usable recipe - grounded in real
        ingredient co-occurrence stats (recipe_invention.build_ingredient_palette) and the
        household's saved preferences, not freeform hallucination."""
        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Invent a Recipe - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <h1>Invent a Recipe <span class="llm-badge" title="Uses an LLM">LLM</span></h1>
            <p class="recipe-meta">Give it some ingredients you have (or want to use) and/or a mood - it'll build a new recipe grounded in ingredient combinations that actually show up together in the collection. Writing the recipe itself is done by an LLM (see <a href="/preferences">Preferences</a> for which provider).</p>
            <form id="invent-form">
                <div class="form-group">
                    <label for="invent-ingredients">Ingredients (comma-separated):</label>
                    <input type="text" id="invent-ingredients" name="ingredients" placeholder="e.g. onion, chickpeas" style="width: 100%;">
                </div>
                <div class="form-group">
                    <label for="invent-mood">Mood / occasion (optional):</label>
                    <input type="text" id="invent-mood" name="mood" placeholder="e.g. something warm and comforting" style="width: 100%;">
                </div>
                <button type="submit" id="invent-btn">Invent a recipe</button>
            </form>
            <div id="invent-result"></div>

            <script>
                function escapeHtml(s) {{
                    const div = document.createElement('div');
                    div.textContent = s;
                    return div.innerHTML;
                }}
                document.getElementById('invent-form').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    const btn = document.getElementById('invent-btn');
                    const resultDiv = document.getElementById('invent-result');
                    const ingredients = document.getElementById('invent-ingredients').value
                        .split(',').map(s => s.trim()).filter(s => s.length > 0);
                    const mood = document.getElementById('invent-mood').value;
                    if (ingredients.length === 0 && mood.trim() === '') {{
                        alert('Give it at least one ingredient or a mood to work from.');
                        return;
                    }}
                    btn.disabled = true;
                    btn.textContent = 'Inventing... (this can take up to a minute)';
                    resultDiv.innerHTML = '';
                    fetch('/api/recipe/invent', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{ingredients, mood}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        btn.disabled = false;
                        btn.textContent = 'Invent a recipe';
                        if (!data.success) {{
                            resultDiv.innerHTML = `<p class="no-directions-note">${{escapeHtml(data.error)}}</p>`;
                            return;
                        }}
                        const r = data.recipe;
                        resultDiv.innerHTML = `
                            <h2>${{escapeHtml(r.title)}}</h2>
                            <p>${{escapeHtml(r.description)}}</p>
                            <p class="recipe-meta">Grounded in: ${{escapeHtml(r.grounded_in.join(', '))}}</p>
                            <ul class="ingredients-list">${{r.ingredients.map(i => `<li>${{escapeHtml(i)}}</li>`).join('')}}</ul>
                            <ol class="instructions-list">${{r.instructions.map(i => `<li>${{escapeHtml(i)}}</li>`).join('')}}</ol>
                            <button id="save-invented-btn">Save as new recipe</button>
                        `;
                        document.getElementById('save-invented-btn').addEventListener('click', function() {{
                            fetch('/api/recipe', {{
                                method: 'POST',
                                headers: {{'Content-Type': 'application/json'}},
                                body: JSON.stringify({{title: r.title, description: r.description, ingredients: r.ingredients, instructions: r.instructions, cuisine: r.cuisine || ''}})
                            }})
                            .then(response => response.json())
                            .then(saveData => {{
                                if (saveData.success) {{
                                    window.location.href = `/recipe/${{saveData.recipe_id}}`;
                                }} else {{
                                    alert('Error: ' + saveData.error);
                                }}
                            }});
                        }});
                    }})
                    .catch(error => {{
                        btn.disabled = false;
                        btn.textContent = 'Invent a recipe';
                        resultDiv.innerHTML = `<p class="no-directions-note">Error: ${{escapeHtml(String(error))}}</p>`;
                    }});
                }});
            </script>
            </main>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_upload(self, filename):
        """The one narrow, explicit exception to this app serving zero
        static files (see the do_GET fallback comment) - locally-uploaded
        recipe photos. Safe because uploads.resolve_upload_path() only
        ever resolves server-generated filenames (see uploads.py's module
        docstring); anything else, including any path-traversal attempt,
        returns None here before the filesystem is touched."""
        path = uploads.resolve_upload_path(filename)
        if path is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-type', uploads.content_type_for(filename))
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def serve_pantry(self):
        """Persistent pantry: what you have on hand, retained across
        visits. Items needing confirmation (approaching/just past typical
        shelf life) are shown separately and first - the app never
        silently assumes stale stock is still good, it asks."""
        items = pantry.get_items()
        needs_confirmation = [i for i in items if i['status'] == 'needs_confirmation']
        fresh = [i for i in items if i['status'] == 'fresh']

        def item_row(item, show_confirm):
            confirm_btn = (
                f'<button onclick="confirmItem({item["id"]})">Still have it</button> '
                if show_confirm else ''
            )
            age_note = f'{item["days_since_added"]:.0f}d old (typical shelf life ~{item["shelf_life_days"]}d, {item["shelf_life_category"]})'
            return (
                f'<li data-item-id="{item["id"]}">'
                f'<strong>{escape_html(item["name"])}</strong> '
                f'<span class="recipe-meta">{escape_html(age_note)}</span><br>'
                f'{confirm_btn}'
                f'<a href="#" class="delete-pantry-link" data-item-id="{item["id"]}">remove</a>'
                '</li>'
            )

        needs_confirmation_html = (
            ''.join(item_row(i, show_confirm=True) for i in needs_confirmation)
            or '<li class="recipe-meta">Nothing needs confirming.</li>'
        )
        fresh_html = ''.join(item_row(i, show_confirm=False) for i in fresh) or '<li class="recipe-meta">Pantry is empty.</li>'

        fresh_names = ','.join(i['name'] for i in fresh)
        discover_link = f'/discover?have={urllib.parse.quote(fresh_names)}' if fresh_names else '/discover'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Pantry - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1>Pantry</h1>
            <p class="recipe-meta">What you have on hand, retained across visits. Checking off a shopping-list item adds it here automatically. Items are never silently assumed fresh forever - they're flagged for confirmation as they approach their typical shelf life, and dropped automatically well past it.</p>

            <p><a href="{discover_link}">Find recipes using my pantry &rarr;</a></p>

            <h2>Needs confirmation</h2>
            <ul class="ingredients-list" id="needs-confirmation-list" aria-live="polite">{needs_confirmation_html}</ul>

            <h2>Fresh</h2>
            <ul class="ingredients-list" id="fresh-list" aria-live="polite">{fresh_html}</ul>

            <div class="form-group">
                <label for="pantry-item-name">Add item:</label>
                <input type="text" id="pantry-item-name" placeholder="e.g. milk">
                <input type="text" id="pantry-item-quantity" placeholder="quantity (optional)" style="max-width:140px;">
                <button onclick="addPantryItem()">Add</button>
            </div>
            </main>

            <script>
                function refreshPantry() {{
                    fetch('/pantry')
                        .then(response => response.text())
                        .then(html => {{
                            const parser = new DOMParser();
                            const doc = parser.parseFromString(html, 'text/html');
                            document.getElementById('needs-confirmation-list').innerHTML = doc.getElementById('needs-confirmation-list').innerHTML;
                            document.getElementById('fresh-list').innerHTML = doc.getElementById('fresh-list').innerHTML;
                        }});
                }}
                function addPantryItem() {{
                    const name = document.getElementById('pantry-item-name').value;
                    const quantity = document.getElementById('pantry-item-quantity').value;
                    if (!name.trim()) return;
                    fetch('/api/pantry', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{name: name, quantity: quantity || null}})
                    }}).then(() => {{
                        document.getElementById('pantry-item-name').value = '';
                        document.getElementById('pantry-item-quantity').value = '';
                        refreshPantry();
                    }});
                }}
                function confirmItem(itemId) {{
                    fetch(`/api/pantry/${{itemId}}`, {{ method: 'PUT' }}).then(() => refreshPantry());
                }}
                document.addEventListener('click', function(e) {{
                    if (e.target.classList.contains('delete-pantry-link')) {{
                        e.preventDefault();
                        const itemId = e.target.dataset.itemId;
                        fetch(`/api/pantry/${{itemId}}`, {{ method: 'DELETE' }}).then(() => refreshPantry());
                    }}
                }});
            </script>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_shopping_lists(self):
        """List all shopping lists, with a small form to create a new one."""
        lists = shopping_list.list_lists()

        def list_card(lst):
            return (
                '<div class="recipe-card">'
                f'<div class="recipe-title"><a href="/list/{lst["id"]}">{escape_html(lst["name"])}</a></div>'
                f'<div class="recipe-meta">{lst["checked_count"]}/{lst["item_count"]} checked off</div>'
                '</div>'
            )

        list_cards = ''.join(list_card(l) for l in lists) or '<p>No shopping lists yet.</p>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Shopping Lists - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1>Shopping Lists</h1>

            <form id="create-list-form">
                <div class="form-group">
                    <label for="list-name">List name:</label>
                    <input type="text" id="list-name" required>
                </div>
                <button type="submit">Create List</button>
            </form>

            <h2>Existing Lists</h2>
            <div id="list-list">{list_cards}</div>

            <script>
                document.getElementById('create-list-form').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    const name = document.getElementById('list-name').value;
                    fetch('/api/shoppinglist', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{name: name}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            window.location.href = '/list/' + data.list_id;
                        }} else {{
                            alert('Failed to create list: ' + data.error);
                        }}
                    }});
                }});
            </script>
            </main>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_shopping_list_detail(self, list_id):
        """View a single shopping list: checkable items, a manual-add form,
        and controls to pull in every ingredient from a recipe or an entire
        meal plan (merging into existing unchecked lines where possible)."""
        lst = shopping_list.get_list(list_id)
        if not lst:
            self.send_response(404)
            self.end_headers()
            return

        items = shopping_list.get_items(list_id)

        def item_row(item):
            checked_attr = 'checked' if item['checked'] else ''
            css_class = 'shopping-item checked' if item['checked'] else 'shopping-item'
            return (
                f'<li class="{css_class}" data-item-id="{item["id"]}">'
                f'<label><input type="checkbox" {checked_attr} onchange="toggleItem({item["id"]}, this.checked)"> '
                f'{escape_html(item["display"])}</label> '
                f'<a href="#" class="delete-item-link" data-item-id="{item["id"]}">remove</a>'
                '</li>'
            )

        items_html = ''.join(item_row(i) for i in items) or '<li class="recipe-meta">No items yet.</li>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>{escape_html(lst["name"])} - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <div class="recipe-header">
                <h1 class="recipe-title">{escape_html(lst["name"])}</h1>
            </div>

            <ul class="ingredients-list" id="item-list" aria-live="polite">{items_html}</ul>

            <div class="form-group">
                <label for="item-name">Add item:</label>
                <input type="text" id="item-name" placeholder="e.g. paper towels">
                <button onclick="addManualItem()">Add</button>
            </div>

            <div class="form-group">
                <label for="add-recipe-id">Add all ingredients from recipe ID:</label>
                <input type="text" id="add-recipe-id" placeholder="e.g. 1226">
                <button onclick="addFromRecipe()">Add</button>
            </div>

            <div class="form-group">
                <label for="add-plan-id">Add all ingredients from meal plan ID:</label>
                <input type="text" id="add-plan-id" placeholder="e.g. 3">
                <button onclick="addFromPlan()">Add</button>
            </div>

            <p><a href="/lists">&larr; Back to all lists</a></p>
            </main>

            <script>
                function refreshItems() {{
                    fetch('/list/{list_id}')
                        .then(response => response.text())
                        .then(html => {{
                            const parser = new DOMParser();
                            const doc = parser.parseFromString(html, 'text/html');
                            document.getElementById('item-list').innerHTML = doc.getElementById('item-list').innerHTML;
                        }});
                }}
                function toggleItem(itemId, checked) {{
                    fetch(`/api/shoppinglist/{list_id}/item/${{itemId}}`, {{
                        method: 'PUT',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{checked: checked}})
                    }}).then(() => refreshItems());
                }}
                function addManualItem() {{
                    const name = document.getElementById('item-name').value;
                    if (!name.trim()) return;
                    fetch('/api/shoppinglist/{list_id}/item', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{name: name}})
                    }}).then(() => {{ document.getElementById('item-name').value = ''; refreshItems(); }});
                }}
                function addFromRecipe() {{
                    const recipeId = document.getElementById('add-recipe-id').value;
                    if (!recipeId.trim()) return;
                    fetch(`/api/shoppinglist/{list_id}/from-recipe/${{recipeId}}`, {{ method: 'POST' }})
                        .then(response => response.json())
                        .then(data => {{
                            if (!data.success) {{ alert('Error: ' + data.error); return; }}
                            document.getElementById('add-recipe-id').value = '';
                            refreshItems();
                        }});
                }}
                function addFromPlan() {{
                    const planId = document.getElementById('add-plan-id').value;
                    if (!planId.trim()) return;
                    fetch(`/api/shoppinglist/{list_id}/from-plan/${{planId}}`, {{ method: 'POST' }})
                        .then(response => response.json())
                        .then(data => {{
                            if (!data.success) {{ alert('Error: ' + data.error); return; }}
                            document.getElementById('add-plan-id').value = '';
                            refreshItems();
                        }});
                }}
                document.getElementById('item-list').addEventListener('click', function(e) {{
                    if (e.target.classList.contains('delete-item-link')) {{
                        e.preventDefault();
                        const itemId = e.target.dataset.itemId;
                        fetch(`/api/shoppinglist/{list_id}/item/${{itemId}}`, {{ method: 'DELETE' }})
                            .then(() => refreshItems());
                    }}
                }});
            </script>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_cooking_history(self):
        """Serve a reverse-chronological list of every logged cook event
        across the whole collection (list view, not a calendar grid)."""
        entries = cooking_log.get_cook_history()
        if entries:
            rows_html = ''
            for entry in entries:
                recipe = db.get_recipe(entry['recipe_id'])
                cooked_at = escape_html(str(entry["cooked_at"]))
                if recipe:
                    rows_html += f'<li>{cooked_at} - <a href="/recipe/{recipe.id}">{escape_html(recipe.title)}</a></li>'
                else:
                    rows_html += f'<li>{cooked_at} - Recipe #{entry["recipe_id"]} (deleted)</li>'
        else:
            rows_html = '<li class="recipe-meta">No cooking history logged yet.</li>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Cooking History - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <h1>Cooking History</h1>
            <ul class="ingredients-list">{rows_html}</ul>
            </main>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_recipes_api(self, query_params=None):
        """Serve one page of recipes as JSON (?page=, 1-indexed)."""
        page = parse_page(query_params)
        recipes = db.get_all_recipes(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, exclude_builtin=hide_builtin_recipes())
        recipes_data = [recipe.to_dict() for recipe in recipes]
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(recipes_data).encode())
    
    def serve_add_recipe_page(self):
        """Direct, discoverable manual recipe entry (GET /add) - the same
        form/endpoint the empty-search-results quick-add box already used,
        just given its own page and a nav link instead of only being
        reachable by searching for something that doesn't exist yet."""
        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Add a Recipe - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <h1>Add a Recipe</h1>
            <p class="recipe-meta">Have a URL instead? Use <a href="/import">Import</a> - it'll pull the title, ingredients, and instructions for you.</p>
            {get_add_recipe_form_html('Recipe details')}
            </main>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_edit_recipe(self, recipe_id):
        """Edit an existing recipe (GET /recipe/<id>/edit). The PUT
        /api/recipe/<id> backend already existed (used by recipe_images.py
        etc. internally) but had no page of its own - this is a new
        form, not a new backend capability. Covers the same content
        fields handle_update_recipe() accepts (title, description,
        ingredients, instructions, prep_time, cook_time, total_time,
        servings, cuisine, difficulty) - deliberately excludes url/
        license/image_url, which aren't recipe *content* and are either
        provenance (url, license, preserved as-is by the backend
        regardless of what's posted) or already have their own dedicated
        UI (image_url, via the photo gallery)."""
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self.send_response(404)
            self.end_headers()
            return

        ingredients_value = '\n'.join(recipe.ingredients)
        instructions_value = '\n'.join(recipe.instructions)

        def difficulty_option(value, label):
            selected = 'selected' if recipe.difficulty == value else ''
            return f'<option value="{value}" {selected}>{label}</option>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Edit {escape_html(recipe.title)} - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <h1>Edit Recipe</h1>
            <form id="edit-recipe-form">
                <div class="form-group">
                    <label for="edit-title">Title:</label>
                    <input type="text" id="edit-title" name="title" value="{escape_html(recipe.title)}" required>
                </div>
                <div class="form-group">
                    <label for="edit-description">Description:</label>
                    <textarea id="edit-description" name="description" rows="2" style="width: 100%;">{escape_html(recipe.description)}</textarea>
                </div>
                <div class="form-group">
                    <label for="edit-ingredients">Ingredients (one per line):</label>
                    <textarea id="edit-ingredients" name="ingredients" rows="10" style="width: 100%;">{escape_html(ingredients_value)}</textarea>
                </div>
                <div class="form-group">
                    <label for="edit-instructions">Instructions (one step per line):</label>
                    <textarea id="edit-instructions" name="instructions" rows="10" style="width: 100%;">{escape_html(instructions_value)}</textarea>
                </div>
                <div class="form-group">
                    <label for="edit-servings">Servings:</label>
                    <input type="number" id="edit-servings" name="servings" value="{recipe.servings}" min="1" style="width: 8em;">
                </div>
                <div class="form-group">
                    <label for="edit-prep-time">Prep time (minutes):</label>
                    <input type="number" id="edit-prep-time" name="prep_time" value="{recipe.prep_time}" min="0" style="width: 8em;">
                </div>
                <div class="form-group">
                    <label for="edit-cook-time">Cook time (minutes):</label>
                    <input type="number" id="edit-cook-time" name="cook_time" value="{recipe.cook_time}" min="0" style="width: 8em;">
                </div>
                <div class="form-group">
                    <label for="edit-total-time">Total time (minutes):</label>
                    <input type="number" id="edit-total-time" name="total_time" value="{recipe.total_time}" min="0" style="width: 8em;">
                </div>
                <div class="form-group">
                    <label for="edit-cuisine">Cuisine:</label>
                    <input type="text" id="edit-cuisine" name="cuisine" value="{escape_html(recipe.cuisine)}" placeholder="e.g. italian, mexican">
                </div>
                <div class="form-group">
                    <label for="edit-difficulty">Difficulty:</label>
                    <select id="edit-difficulty" name="difficulty">
                        <option value="" {"selected" if not recipe.difficulty else ""}>-</option>
                        {difficulty_option("easy", "Easy")}
                        {difficulty_option("medium", "Medium")}
                        {difficulty_option("hard", "Hard")}
                    </select>
                </div>
                <button type="submit">Save Changes</button>
                <a href="/recipe/{recipe.id}" class="print-button">Cancel</a>
            </form>

            <script>
                document.getElementById('edit-recipe-form').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    const button = e.target.querySelector('button[type=submit]');
                    button.disabled = true;
                    button.textContent = 'Saving...';

                    const body = {{
                        title: document.getElementById('edit-title').value,
                        description: document.getElementById('edit-description').value,
                        ingredients: document.getElementById('edit-ingredients').value
                            .split('\\n').map(s => s.trim()).filter(s => s.length > 0),
                        instructions: document.getElementById('edit-instructions').value
                            .split('\\n').map(s => s.trim()).filter(s => s.length > 0),
                        servings: parseInt(document.getElementById('edit-servings').value, 10) || 1,
                        prep_time: parseInt(document.getElementById('edit-prep-time').value, 10) || 0,
                        cook_time: parseInt(document.getElementById('edit-cook-time').value, 10) || 0,
                        total_time: parseInt(document.getElementById('edit-total-time').value, 10) || 0,
                        cuisine: document.getElementById('edit-cuisine').value.trim(),
                        difficulty: document.getElementById('edit-difficulty').value,
                    }};

                    fetch('/api/recipe/{recipe.id}', {{
                        method: 'PUT',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(body)
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            window.location.href = '/recipe/{recipe.id}';
                        }} else {{
                            alert('Error: ' + data.error);
                            button.disabled = false;
                            button.textContent = 'Save Changes';
                        }}
                    }})
                    .catch(error => {{
                        alert('Error: ' + error);
                        button.disabled = false;
                        button.textContent = 'Save Changes';
                    }});
                }});
            </script>
            </main>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_import_page(self):
        """Serve the import page."""
        html = '''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Import Recipe - Sous</title>
            <meta charset="UTF-8">
            ''' + get_base_style() + '''
        </head>
        <body>
            ''' + get_nav_html() + '''
            <main id="main-content">
            <h1>Import Recipe</h1>

            <form id="import-form">
                <div class="form-group">
                    <label for="recipe-url">Recipe URL:</label>
                    <input type="url" id="recipe-url" name="url" required>
                </div>
                <button type="submit">Import Recipe</button>
            </form>

            <h2>Bulk import: Paprika export</h2>
            <p class="recipe-meta">A .paprikarecipes file exported from the Paprika app.</p>
            <form id="paprika-form">
                <div class="form-group">
                    <label for="paprika-file" class="sr-only">Paprika export file</label>
                    <input type="file" id="paprika-file" accept=".paprikarecipes" required>
                </div>
                <button type="submit">Import Paprika File</button>
            </form>

            <h2>Bulk import: generic recipe JSON</h2>
            <p class="recipe-meta">A JSON file containing one schema.org-shaped recipe object, or a list of them.</p>
            <form id="bulk-form">
                <div class="form-group">
                    <label for="bulk-file" class="sr-only">Bulk recipe JSON file</label>
                    <input type="file" id="bulk-file" accept=".json" required>
                </div>
                <button type="submit">Import JSON File</button>
            </form>

            <p><a href="/">← Back to recipes</a></p>
            </main>

            <script>
                function setSubmitting(form, isSubmitting, busyText) {
                    const button = form.querySelector('button[type="submit"]');
                    if (isSubmitting) {
                        button.dataset.originalText = button.textContent;
                        button.textContent = busyText;
                        button.disabled = true;
                    } else {
                        button.textContent = button.dataset.originalText || button.textContent;
                        button.disabled = false;
                    }
                }

                const importForm = document.getElementById('import-form');
                importForm.addEventListener('submit', function(e) {
                    e.preventDefault();
                    const url = document.getElementById('recipe-url').value;

                    setSubmitting(importForm, true, 'Importing…');
                    fetch('/api/recipe/import', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({url: url})
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            alert('Recipe imported successfully!');
                            window.location.href = `/recipe/${data.recipe_id}`;
                        } else {
                            alert('Failed to import recipe: ' + data.error);
                            setSubmitting(importForm, false);
                        }
                    })
                    .catch(err => {
                        alert('Import failed: ' + err.message);
                        setSubmitting(importForm, false);
                    });
                });

                function submitFileImport(formId, fileInputId, endpoint) {
                    const form = document.getElementById(formId);
                    form.addEventListener('submit', function(e) {
                        e.preventDefault();
                        const fileInput = document.getElementById(fileInputId);
                        const file = fileInput.files[0];
                        if (!file) return;
                        setSubmitting(form, true, 'Importing…');
                        const reader = new FileReader();
                        reader.onload = function() {
                            const base64 = reader.result.split(',')[1];
                            fetch(endpoint, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ file_base64: base64 })
                            })
                            .then(response => response.json())
                            .then(data => {
                                if (data.success) {
                                    alert(`Imported ${data.count} recipe(s).`);
                                    window.location.href = '/';
                                } else {
                                    alert('Import failed: ' + data.error);
                                    setSubmitting(form, false);
                                }
                            })
                            .catch(err => {
                                alert('Import failed: ' + err.message);
                                setSubmitting(form, false);
                            });
                        };
                        reader.onerror = function() {
                            alert('Failed to read file: ' + reader.error);
                            setSubmitting(form, false);
                        };
                        reader.readAsDataURL(file);
                    });
                }
                submitFileImport('paprika-form', 'paprika-file', '/api/recipe/import/paprika');
                submitFileImport('bulk-form', 'bulk-file', '/api/recipe/import/bulk');
            </script>
        </body>
        </html>
        '''
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def serve_print_view(self, recipe_id, query_params=None):
        """Serve a print-friendly version of a recipe.

        Supports customization via query params (?id=..&images=0/1&
        nutrition=0/1&font=normal/large&layout=standard/compact), applied
        via a no-print toggle form on the page itself. `images`/`nutrition`
        toggle whether those sections render. Real data covers ~75% of
        recipes (backfilled from the datahiveai and AkashPS11 source
        datasets - see backfill_nutrition.py); the remaining ~13,495
        Hieu-Pham-sourced recipes have neither in the source data, so the
        toggle honestly falls back to a no-data note for those.
        """
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self.send_response(404)
            self.end_headers()
            return

        query_params = query_params or {}

        def toggle(name, default, allowed=None):
            values = query_params.get(name)
            if not values:
                return default
            value = values[-1]  # hidden+checkbox pairs send [hidden, checked]; last wins
            if allowed is not None and value not in allowed:
                return default
            return value

        show_images = toggle('images', '1', ('0', '1')) == '1'
        show_nutrition = toggle('nutrition', '0', ('0', '1')) == '1'
        font_size = toggle('font', 'normal', ('normal', 'large'))
        layout = toggle('layout', 'standard', ('standard', 'compact'))

        # Structured (parsed) rendering, same as the recipe detail page -
        # print view has no servings scaling of its own, so this is always
        # factor=1.0, but it keeps the amount formatting consistent with
        # the detail page instead of showing raw unparsed text here.
        structured_ingredients = db.get_structured_ingredients(recipe.id)
        display_ingredients = scale_recipe_to_servings_structured(structured_ingredients, recipe.servings, recipe.servings)

        if recipe.instructions:
            instructions_html = f'''<ol class="instructions-list">
                    {''.join(f'<li>{escape_html(instruction)}</li>' for instruction in recipe.instructions)}
                </ol>'''
        else:
            instructions_html = '<p class="no-directions-note">Ingredients only - no directions available for this recipe.</p>'

        if show_images and recipe.image_url:
            image_html = f'<img src="{escape_html(recipe.image_url)}" alt="{escape_html(recipe.title)}" class="print-recipe-image">'
        elif show_images:
            image_html = '<p class="no-print print-options-note">(no image available for this recipe)</p>'
        else:
            image_html = ''

        if show_nutrition and recipe.nutrition:
            nutrition_html = f'<div class="nutrition"><h2>Nutrition</h2><p>{escape_html(recipe.nutrition)}</p></div>'
        elif show_nutrition:
            nutrition_html = '<p class="no-print print-options-note">(no nutrition data available for this recipe)</p>'
        else:
            nutrition_html = ''

        font_css = '''
        <style>
            body { font-size: 17px; }
            @media print { body { font-size: 14pt; } }
        </style>
        ''' if font_size == 'large' else ''

        layout_css = '''
        <style>
            .recipe-header { padding-bottom: 4px; }
            .ingredients, .instructions, .nutrition { margin: 8px 0; }
            .ingredients-list, .instructions-list { padding-left: 16px; }
            .ingredients-list li, .instructions-list li { margin-bottom: 2px; }
            h2 { margin: 8px 0 4px; }
        </style>
        ''' if layout == 'compact' else ''

        def selected(current, value):
            return 'selected' if current == value else ''

        def checked(current):
            return 'checked' if current else ''

        print_options_html = f'''
        <form method="get" action="/print" class="no-print print-options">
            <input type="hidden" name="id" value="{recipe.id}">
            <label>Layout:
                <select name="layout" onchange="this.form.submit()">
                    <option value="standard" {selected(layout, 'standard')}>Standard</option>
                    <option value="compact" {selected(layout, 'compact')}>Compact</option>
                </select>
            </label>
            <label>Font size:
                <select name="font" onchange="this.form.submit()">
                    <option value="normal" {selected(font_size, 'normal')}>Normal</option>
                    <option value="large" {selected(font_size, 'large')}>Large</option>
                </select>
            </label>
            <label>
                <input type="hidden" name="images" value="0">
                <input type="checkbox" name="images" value="1" {checked(show_images)} onchange="this.form.submit()">
                Show image
            </label>
            <label>
                <input type="hidden" name="nutrition" value="0">
                <input type="checkbox" name="nutrition" value="1" {checked(show_nutrition)} onchange="this.form.submit()">
                Show nutrition
            </label>
        </form>
        '''

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>{escape_html(recipe.title)} - Print View</title>
            <meta charset="UTF-8">
            {get_base_style()}
            {font_css}
            {layout_css}
        </head>
        <body>
            {print_options_html}

            <main id="main-content">
            <div class="recipe-header">
                <h1 class="recipe-title">{escape_html(recipe.title)}</h1>
                <p class="recipe-meta">{escape_html(recipe.cuisine)} | {recipe.servings} servings |
                Prep: {recipe.prep_time} min | Cook: {recipe.cook_time} min</p>
            </div>

            {image_html}

            <p>{escape_html(recipe.description)}</p>

            <div class="ingredients">
                <h2>Ingredients</h2>
                <ul class="ingredients-list">
                    {ingredients_list_html(structured_ingredients, display_ingredients)}
                </ul>
            </div>

            <div class="instructions">
                <h2>Instructions</h2>
                {instructions_html}
            </div>

            {nutrition_html}

            <p class="no-print"><a href="javascript:window.print()">Print this page</a></p>
            </main>
        </body>
        </html>
        '''

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_plans_list(self):
        """List all meal plans, with a small form to create a new one."""
        plans = meal_db.list_plans()

        def plan_card(plan):
            recipe_count = len(meal_db.get_plan_recipe_ids(plan['id']))
            eat_time = plan['target_eat_time'] or 'not set'
            return (
                '<div class="recipe-card">'
                f'<div class="recipe-title"><a href="/plan/{plan["id"]}">{escape_html(plan["name"])}</a></div>'
                f'<div class="recipe-meta">{recipe_count} recipe(s) - eat at {escape_html(eat_time)}</div>'
                '</div>'
            )

        plan_cards = ''.join(plan_card(p) for p in plans) or '<p>No meal plans yet.</p>'

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>Meal Plans - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body class="list-page">
            {get_nav_html()}
            <main id="main-content">
            <h1>Meal Plans</h1>

            <form id="create-plan-form">
                <div class="form-group">
                    <label for="plan-name">Plan name:</label>
                    <input type="text" id="plan-name" required>
                </div>
                <div class="form-group">
                    <label for="plan-eat-time">Target eat time (24h, e.g. 18:00):</label>
                    <input type="text" id="plan-eat-time" placeholder="18:00">
                </div>
                <button type="submit">Create Plan</button>
            </form>

            <h2>Existing Plans</h2>
            <div id="plan-list">{plan_cards}</div>

            <script>
                document.getElementById('create-plan-form').addEventListener('submit', function(e) {{
                    e.preventDefault();
                    const name = document.getElementById('plan-name').value;
                    const eatTime = document.getElementById('plan-eat-time').value;
                    fetch('/api/plan', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{name: name, target_eat_time: eatTime}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            window.location.href = '/plan/' + data.plan_id;
                        }} else {{
                            alert('Failed to create plan: ' + data.error);
                        }}
                    }});
                }});
            </script>
            </main>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def _render_plan_fragments(self, plan_id):
        """Build the three dynamic sections of a plan-detail page (recipe
        list, companion suggestions, cooking timeline) as escape_html()-safe
        HTML strings. Shared by serve_plan_detail (full page) and
        serve_plan_fragment (the JSON endpoint addRecipe/removeRecipe use to
        refresh those sections without a full page reload) - the companion-
        suggestion and backward-scheduling algorithms are real logic that
        must not be duplicated client-side. Returns None if the plan doesn't
        exist."""
        plan = meal_db.get_plan(plan_id)
        if not plan:
            return None

        recipe_ids = meal_db.get_plan_recipe_ids(plan_id)
        recipes = [db.get_recipe(rid) for rid in recipe_ids]
        recipes = [r for r in recipes if r]

        def recipe_row(recipe):
            return (
                '<div class="recipe-card">'
                f'{recipe_thumb_html(recipe.image_url, recipe.title)}'
                '<div class="recipe-card-body">'
                f'<div class="recipe-title"><a href="/recipe/{recipe.id}">{escape_html(recipe.title)}</a></div>'
                f'<div class="recipe-meta">{escape_html(recipe.cuisine)} | {recipe.servings} servings</div>'
                f'<button onclick="removeRecipe({recipe.id})">Remove from plan</button>'
                '</div>'
                '</div>'
            )

        recipe_rows = ''.join(recipe_row(r) for r in recipes) or '<p>No recipes in this plan yet.</p>'

        # Backward-schedule timeline, if a target eat time is set.
        schedule_html = '<p>Set a target eat time above to see a cooking timeline.</p>'
        if plan['target_eat_time'] and recipes:
            try:
                hour, minute = plan['target_eat_time'].split(':')
                eat_time = datetime.now().replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
                result = meal_db.backward_schedule_plan(plan_id, db, eat_time)

                if not result['timeline']:
                    schedule_html = '<p class="no-directions-note">None of the recipes in this plan have directions to schedule (they may be ingredients-only entries).</p>'
                else:
                    rows = ''.join(
                        f'<li>{s["start_time"].strftime("%H:%M")} - {s["end_time"].strftime("%H:%M")} '
                        f'[{escape_html(s["step_type"])}] <strong>{escape_html(s["recipe_title"])}</strong>: {escape_html(s["text"])}</li>'
                        for s in result['timeline']
                    )
                    schedule_html = f'<p><em>Estimated from recipe text - durations and active/passive steps are heuristic, not exact.</em></p><ol class="instructions-list">{rows}</ol>'

                    if result['conflicts']:
                        conflict_rows = ''.join(
                            f'<li>{c["overlap_start"].strftime("%H:%M")}-{c["overlap_end"].strftime("%H:%M")}: '
                            f'"{escape_html(c["a"])}" overlaps with "{escape_html(c["b"])}" - you\'ll need to sequence these or multitask</li>'
                            for c in result['conflicts']
                        )
                        schedule_html += f'<p class="no-directions-note"><strong>Heads up - active-step conflicts:</strong><ul>{conflict_rows}</ul></p>'

                    if result['skipped_no_instructions']:
                        skipped = ', '.join(escape_html(title) for title in result['skipped_no_instructions'])
                        schedule_html += f'<p><em>Not scheduled (no directions available): {skipped}</em></p>'
            except (ValueError, IndexError):
                schedule_html = '<p class="no-directions-note">Invalid target eat time format - use HH:MM, e.g. 18:00.</p>'

        # Companion suggestions, based on the most recently added recipe.
        suggestions_html = '<p>Add a recipe to see suggested companions.</p>'
        if recipes:
            suggestions = meal_db.suggest_companions(recipes[-1], db, limit=5)
            if suggestions:
                seed_flavors = suggestions[0].get('seed_flavor_profile') or []
                seed_flavor_html = (
                    f' <span class="flavor-tags">(leans {escape_html(", ".join(seed_flavors))})</span>' if seed_flavors else ''
                )
                rows = ''.join(
                    f'<li><a href="/recipe/{s["id"]}">{escape_html(s["title"])}</a> '
                    + (f'<span class="flavor-tags">{escape_html(", ".join(s["flavor_profile"]))}</span> ' if s.get("flavor_profile") else '')
                    + f'<button onclick="addRecipe({s["id"]})">Add to plan</button></li>'
                    for s in suggestions
                )
                suggestions_html = f'<p>Pairs well with <strong>{escape_html(recipes[-1].title)}</strong>{seed_flavor_html}:</p><ul>{rows}</ul>'
            else:
                suggestions_html = f'<p>No companion suggestions found for {escape_html(recipes[-1].title)}.</p>'

        return {'plan': plan, 'recipe_rows': recipe_rows, 'suggestions_html': suggestions_html, 'schedule_html': schedule_html}

    def serve_plan_fragment(self, plan_id):
        """JSON endpoint (GET /api/plan/<id>/fragment) returning the three
        dynamic sections of a plan-detail page as pre-escaped HTML strings,
        so addRecipe()/removeRecipe() can refresh them in place instead of
        reloading the whole page."""
        fragments = self._render_plan_fragments(plan_id)
        if fragments is None:
            self._respond_json(404, {'success': False, 'error': f'Plan {plan_id} not found'})
            return
        self._respond_json(200, {
            'success': True,
            'recipe_rows': fragments['recipe_rows'],
            'suggestions_html': fragments['suggestions_html'],
            'schedule_html': fragments['schedule_html'],
        })

    def serve_plan_detail(self, plan_id):
        """View a single meal plan: its recipes, a backward-scheduling
        timeline against the target eat time (if set), and companion
        suggestions for the most recently added recipe."""
        fragments = self._render_plan_fragments(plan_id)
        if fragments is None:
            self.send_response(404)
            self.end_headers()
            return
        plan = fragments['plan']
        recipe_rows = fragments['recipe_rows']
        suggestions_html = fragments['suggestions_html']
        schedule_html = fragments['schedule_html']

        html = f'''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <title>{escape_html(plan["name"])} - Sous</title>
            <meta charset="UTF-8">
            {get_base_style()}
        </head>
        <body>
            {get_nav_html()}
            <main id="main-content">
            <div class="recipe-header">
                <h1 class="recipe-title">{escape_html(plan["name"])}</h1>
                <p class="recipe-meta">Target eat time: {escape_html(plan["target_eat_time"] or "not set")}</p>
            </div>

            <h2>Recipes in this plan</h2>
            <div id="recipe-list" aria-live="polite">{recipe_rows}</div>

            <div class="form-group">
                <label for="add-recipe-id">Add recipe by ID:</label>
                <input type="text" id="add-recipe-id" placeholder="e.g. 1226">
                <button onclick="addRecipe(document.getElementById('add-recipe-id').value)">Add</button>
            </div>

            <p><button onclick="generateShoppingList()">Generate shopping list from this plan</button></p>

            <h2>Suggested Companions</h2>
            <div id="suggestions" aria-live="polite">{suggestions_html}</div>

            <h2>Cooking Timeline</h2>
            <div id="schedule" aria-live="polite">{schedule_html}</div>

            <p><a href="/plans">&larr; Back to all plans</a></p>
            </main>

            <script>
                function generateShoppingList() {{
                    fetch('/api/shoppinglist', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{name: {json.dumps(plan["name"])} + ' - shopping list'}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (!data.success) {{ alert('Error: ' + data.error); return; }}
                        return fetch(`/api/shoppinglist/${{data.list_id}}/from-plan/{plan_id}`, {{ method: 'POST' }})
                            .then(() => {{ window.location.href = '/list/' + data.list_id; }});
                    }});
                }}
                function refreshPlanFragments() {{
                    // Re-fetches the three server-computed dynamic sections
                    // (companion suggestions and the backward-scheduled
                    // timeline are real algorithms, not something to
                    // reimplement client-side) and patches them in place.
                    // Safe to set via innerHTML specifically because this
                    // HTML was already run through escape_html() server-side
                    // before being sent - not because innerHTML is safe in
                    // general.
                    return fetch('/api/plan/{plan_id}/fragment')
                        .then(response => response.json())
                        .then(data => {{
                            if (!data.success) return;
                            document.getElementById('recipe-list').innerHTML = data.recipe_rows;
                            document.getElementById('suggestions').innerHTML = data.suggestions_html;
                            document.getElementById('schedule').innerHTML = data.schedule_html;
                        }});
                }}
                function addRecipe(recipeId) {{
                    fetch('/api/plan/{plan_id}/recipe', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{recipe_id: parseInt(recipeId)}})
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            refreshPlanFragments();
                        }} else {{
                            alert('Failed to add recipe: ' + data.error);
                        }}
                    }});
                }}
                function removeRecipe(recipeId) {{
                    if (!confirm('Remove this recipe from the plan?')) return;
                    fetch('/api/plan/{plan_id}/recipe/' + recipeId, {{ method: 'DELETE' }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            refreshPlanFragments();
                        }} else {{
                            alert('Failed to remove recipe: ' + data.error);
                        }}
                    }});
                }}
            </script>
        </body>
        </html>
        '''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def _read_json_body(self):
        """Read and parse the request body as JSON. Returns None (and has
        already sent a 400 response) if the body is missing or invalid."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                raise ValueError("missing or empty request body")
            post_data = self.rfile.read(content_length)
            return json.loads(post_data.decode('utf-8'))
        except (ValueError, json.JSONDecodeError) as e:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': f'Invalid request body: {e}'}).encode())
            return None

    def handle_update_recipe(self, recipe_id):
        """Handle updating an existing recipe via API (PUT /api/recipe/<id>)."""
        existing = db.get_recipe(recipe_id)
        if not existing:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': f'Recipe {recipe_id} not found'}).encode())
            return

        data = self._read_json_body()
        if data is None:
            return

        title = data.get('title', existing.title).strip()
        if not title:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': 'title is required'}).encode())
            return

        recipe = Recipe(
            id=recipe_id,
            title=title,
            description=data.get('description', existing.description),
            ingredients=data.get('ingredients', existing.ingredients),
            instructions=data.get('instructions', existing.instructions),
            prep_time=data.get('prep_time', existing.prep_time),
            cook_time=data.get('cook_time', existing.cook_time),
            total_time=data.get('total_time', existing.total_time),
            servings=data.get('servings', existing.servings),
            cuisine=data.get('cuisine', existing.cuisine),
            difficulty=data.get('difficulty', existing.difficulty),
            url=existing.url,
            created_at=existing.created_at,
            license=existing.license
        )

        try:
            db.save_recipe(recipe)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'recipe_id': recipe_id}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())

    def handle_delete_recipe(self, recipe_id):
        """Handle deleting a recipe via API (DELETE /api/recipe/<id>)."""
        try:
            deleted = db.delete_recipe(recipe_id)
            if deleted:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())
            else:
                self.send_response(404)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': f'Recipe {recipe_id} not found'}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())

    def handle_invent_recipe(self):
        """Generate a new grounded recipe via the local LLM (POST /api/recipe/invent).
        Does not save anything - mirrors handle_adapt_recipe's "review, then save" flow."""
        data = self._read_json_body()
        if data is None:
            return

        seed_ingredients = [str(i) for i in (data.get('ingredients') or []) if str(i).strip()]
        mood = str(data.get('mood', '') or '')

        recipe = invent_recipe(seed_ingredients=seed_ingredients, mood=mood, meal_db=meal_db)
        if recipe is None:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': False,
                'error': 'Could not invent a recipe - give it at least one recognized ingredient, or the local model is unreachable.',
            }).encode())
            return

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'recipe': recipe}).encode())

    def handle_adapt_recipe(self, recipe_id):
        """Rewrite a recipe's ingredients/instructions to fit saved preferences via the
        local LLM (POST /api/recipe/<id>/adapt). Does not save anything - the client
        offers a "Save as new recipe" action that POSTs the result to the existing
        /api/recipe endpoint."""
        recipe = db.get_recipe(recipe_id)
        if not recipe:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': f'Recipe {recipe_id} not found'}).encode())
            return

        adapted = adapt_recipe_to_preferences(recipe)
        if adapted is None:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': False,
                'error': 'Could not adapt this recipe - set some preferences first, or the local model is unreachable.',
            }).encode())
            return

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'adapted': adapted}).encode())

    def handle_save_preferences(self):
        """Save household dietary restrictions / disliked ingredients / free-text rules
        (POST /api/preferences). llm_api_key (if present and non-blank) is routed to
        llm_credentials.py, not prefs_module.save_preferences() - it must never land in
        recipes.db, which is git-tracked in this project. Blank/absent leaves whatever key
        is already stored untouched (the Preferences page's "leave blank to keep" field)."""
        data = self._read_json_body()
        if data is None:
            return

        saved = prefs_module.save_preferences(
            dietary_restrictions=data.get('dietary_restrictions', []),
            disliked_ingredients=data.get('disliked_ingredients', []),
            notes=data.get('notes', ''),
            hide_builtin_recipes=data.get('hide_builtin_recipes'),
            llm_provider=data.get('llm_provider'),
            llm_model=data.get('llm_model'),
            ollama_host=data.get('ollama_host'),
        )
        llm_api_key = data.get('llm_api_key')
        if llm_api_key and saved.get('llm_provider'):
            llm_credentials.save_api_key(saved['llm_provider'], llm_api_key)
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'preferences': saved}).encode())

    def handle_test_llm_connection(self):
        """Test whatever's currently in the LLM Provider form fields (POST
        /api/preferences/test-llm) - not the saved config, since the whole
        point is validating before saving. A blank api_key/ollama_host in
        the request falls back to whatever's already stored/env-configured,
        same as save does."""
        data = self._read_json_body()
        if data is None:
            return
        ok, message = llm_client.test_connection(
            provider=data.get('provider', ''),
            model=data.get('model', ''),
            api_key=data.get('api_key', ''),
            ollama_host=data.get('ollama_host', ''),
        )
        self._respond_json(200, {'success': ok, 'message': message})

    def handle_add_note(self, recipe_id):
        """Add a note to a recipe (POST /api/recipe/<id>/note)."""
        if not db.get_recipe(recipe_id):
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': f'Recipe {recipe_id} not found'}).encode())
            return

        data = self._read_json_body()
        if data is None:
            return

        note_text = data.get('note_text', '').strip()
        if not note_text:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': 'note_text is required'}).encode())
            return

        note_id = cooking_log.add_note(recipe_id, note_text)
        self.send_response(201)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'note_id': note_id}).encode())

    def handle_delete_note(self, recipe_id, note_id):
        """Delete a note (DELETE /api/recipe/<id>/note/<note_id>)."""
        cooking_log.delete_note(note_id)
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True}).encode())

    def handle_add_recipe_image(self, recipe_id):
        """Add a photo to a recipe (POST /api/recipe/<id>/image). Body is
        either {"url": "..."} or {"file_base64": "..."} - whichever is
        present wins; url takes priority if both are somehow sent."""
        if not db.get_recipe(recipe_id):
            self._respond_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        data = self._read_json_body()
        if data is None:
            return

        url = (data.get('url') or '').strip()
        if url:
            image_id = recipe_images.add_image_url(recipe_id, url)
            self._respond_json(201, {'success': True, 'image_id': image_id})
            return

        file_b64 = data.get('file_base64', '')
        if not file_b64:
            self._respond_json(400, {'success': False, 'error': 'url or file_base64 is required'})
            return
        try:
            filename = uploads.save_upload(file_b64)
        except ValueError as e:
            self._respond_json(400, {'success': False, 'error': str(e)})
            return
        image_id = recipe_images.add_image_upload(recipe_id, filename)
        self._respond_json(201, {'success': True, 'image_id': image_id})

    def handle_delete_recipe_image(self, recipe_id, image_id):
        """Remove a photo (DELETE /api/recipe/<id>/image/<image_id>).
        Deletes the underlying uploaded file too if this was a local
        upload, not just the DB row."""
        removed_filename = recipe_images.remove_image(image_id)
        if removed_filename is None:
            self._respond_json(404, {'success': False, 'error': f'Image {image_id} not found'})
            return
        if removed_filename:
            uploads.delete_upload(removed_filename)
        self._respond_json(200, {'success': True})

    def handle_log_cooked(self, recipe_id):
        """Mark a recipe as cooked (POST /api/recipe/<id>/cook)."""
        if not db.get_recipe(recipe_id):
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': f'Recipe {recipe_id} not found'}).encode())
            return

        data = self._read_json_body()
        if data is None:
            return

        cooked_at = data.get('cooked_at') or None
        entry_id = cooking_log.log_cooked(recipe_id, cooked_at)
        self.send_response(201)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'entry_id': entry_id}).encode())

    def handle_delete_cook_log_entry(self, recipe_id, entry_id):
        """Delete a cook-log entry (DELETE /api/recipe/<id>/cook/<entry_id>)."""
        cooking_log.delete_cook_log_entry(entry_id)
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True}).encode())

    def handle_create_recipe(self):
        """Handle creating a new recipe via API."""
        data = self._read_json_body()
        if data is None:
            return

        title = data.get('title', '').strip()
        if not title:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': 'title is required'}).encode())
            return

        # Create recipe from data
        recipe = Recipe(
            title=title,
            description=data.get('description', ''),
            ingredients=data.get('ingredients', []),
            instructions=data.get('instructions', []),
            prep_time=data.get('prep_time', 0),
            cook_time=data.get('cook_time', 0),
            total_time=data.get('total_time', 0),
            servings=data.get('servings', 1),
            cuisine=data.get('cuisine', ''),
            difficulty=data.get('difficulty', ''),
            license='user-imported'
        )

        try:
            recipe_id = db.save_recipe(recipe)
            self.send_response(201)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'success': True, 'recipe_id': recipe_id}
            self.wfile.write(json.dumps(response).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'success': False, 'error': str(e)}
            self.wfile.write(json.dumps(response).encode())

    def handle_import_recipe(self):
        """Handle importing a recipe from URL."""
        data = self._read_json_body()
        if data is None:
            return

        url = data.get('url', '').strip()
        if not url:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': 'url is required'}).encode())
            return

        try:
            recipe_id = import_recipe_from_url(url)
            if recipe_id is None:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'error': f'No recipe found at {url} (no schema.org Recipe markup, or the page could not be fetched)'}
                self.wfile.write(json.dumps(response).encode())
            else:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': True, 'recipe_id': recipe_id}
                self.wfile.write(json.dumps(response).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'success': False, 'error': str(e)}
            self.wfile.write(json.dumps(response).encode())

    def _respond_json(self, status, payload):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def handle_import_paprika(self):
        """Bulk-import a Paprika .paprikarecipes export (POST
        /api/recipe/import/paprika, body: {"file_base64": "..."}). Binary
        zip archive, so it comes in base64-encoded JSON rather than a raw
        upload - avoids hand-rolling multipart/form-data parsing, which
        Python's stdlib http.server has no built-in support for."""
        data = self._read_json_body()
        if data is None:
            return
        file_b64 = data.get('file_base64', '')
        if not file_b64:
            self._respond_json(400, {'success': False, 'error': 'file_base64 is required'})
            return
        try:
            file_bytes = base64.b64decode(file_b64)
        except Exception:
            self._respond_json(400, {'success': False, 'error': 'file_base64 could not be decoded'})
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.paprikarecipes', delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            recipe_ids = import_paprika_file(tmp_path)
            self._respond_json(200, {'success': True, 'recipe_ids': recipe_ids, 'count': len(recipe_ids)})
        except Exception as e:
            self._respond_json(500, {'success': False, 'error': str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    def handle_import_bulk(self):
        """Bulk-import a generic schema.org-shaped recipe JSON file (POST
        /api/recipe/import/bulk, body: {"file_base64": "..."})."""
        data = self._read_json_body()
        if data is None:
            return
        file_b64 = data.get('file_base64', '')
        if not file_b64:
            self._respond_json(400, {'success': False, 'error': 'file_base64 is required'})
            return
        try:
            file_bytes = base64.b64decode(file_b64)
        except Exception:
            self._respond_json(400, {'success': False, 'error': 'file_base64 could not be decoded'})
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.json', mode='wb', delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            recipe_ids = import_bulk_file(tmp_path)
            self._respond_json(200, {'success': True, 'recipe_ids': recipe_ids, 'count': len(recipe_ids)})
        except Exception as e:
            self._respond_json(500, {'success': False, 'error': str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    def handle_create_plan(self):
        """Create a new meal plan (POST /api/plan)."""
        data = self._read_json_body()
        if data is None:
            return

        name = data.get('name', '').strip()
        if not name:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': 'name is required'}).encode())
            return

        target_eat_time = data.get('target_eat_time', '').strip()
        try:
            plan_id = meal_db.create_plan(name, target_eat_time)
            self.send_response(201)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'plan_id': plan_id}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())

    def handle_add_recipe_to_plan(self, plan_id):
        """Add a recipe to a meal plan (POST /api/plan/<id>/recipe)."""
        if not meal_db.get_plan(plan_id):
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': f'Plan {plan_id} not found'}).encode())
            return

        data = self._read_json_body()
        if data is None:
            return

        recipe_id = data.get('recipe_id')
        if not recipe_id or not db.get_recipe(recipe_id):
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': f'Recipe {recipe_id} not found'}).encode())
            return

        try:
            meal_db.add_recipe_to_plan(plan_id, recipe_id)
            self.send_response(201)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())

    def handle_remove_recipe_from_plan(self, plan_id, recipe_id):
        """Remove a recipe from a meal plan (DELETE /api/plan/<id>/recipe/<recipe_id>)."""
        try:
            removed = meal_db.remove_recipe_from_plan(plan_id, recipe_id)
            self.send_response(200 if removed else 404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': removed}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())

    def handle_delete_plan(self, plan_id):
        """Delete a whole meal plan (DELETE /api/plan/<id>)."""
        try:
            deleted = meal_db.delete_plan(plan_id)
            self.send_response(200 if deleted else 404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': deleted}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())

    def handle_create_shopping_list(self):
        """Create a new shopping list (POST /api/shoppinglist)."""
        data = self._read_json_body()
        if data is None:
            return
        name = (data.get('name') or '').strip()
        if not name:
            self._respond_json(400, {'success': False, 'error': 'name is required'})
            return
        try:
            list_id = shopping_list.create_list(name)
            self._respond_json(201, {'success': True, 'list_id': list_id})
        except Exception as e:
            self._respond_json(500, {'success': False, 'error': str(e)})

    def handle_delete_shopping_list(self, list_id):
        """Delete a whole shopping list (DELETE /api/shoppinglist/<id>)."""
        deleted = shopping_list.delete_list(list_id)
        self._respond_json(200 if deleted else 404, {'success': deleted})

    def handle_add_shopping_list_item(self, list_id):
        """Add a manual (not recipe-derived) item (POST /api/shoppinglist/<id>/item)."""
        if not shopping_list.get_list(list_id):
            self._respond_json(404, {'success': False, 'error': f'List {list_id} not found'})
            return
        data = self._read_json_body()
        if data is None:
            return
        name = (data.get('name') or '').strip()
        if not name:
            self._respond_json(400, {'success': False, 'error': 'name is required'})
            return
        item_id = shopping_list.add_manual_item(list_id, name, data.get('quantity'), data.get('unit'))
        self._respond_json(201, {'success': True, 'item_id': item_id})

    def handle_toggle_shopping_list_item(self, list_id, item_id):
        """Toggle an item's checked state (PUT /api/shoppinglist/<id>/item/<item_id>, body: {checked: bool}).
        Checking an item ON is treated as "I just bought this" and
        refreshes/adds it to the pantry - the natural restock signal,
        requiring no extra user effort. Checking OFF does not remove it
        from the pantry (unchecking is "I changed my mind about buying
        it," not "I used up what I already had")."""
        data = self._read_json_body()
        if data is None:
            return
        checked = bool(data.get('checked'))
        item_name = shopping_list.get_item_name(item_id) if checked else None
        changed = shopping_list.set_item_checked(item_id, checked)
        if changed and checked and item_name:
            pantry.add_or_refresh_item(item_name, source='shopping_list')
        self._respond_json(200 if changed else 404, {'success': changed})

    def handle_delete_shopping_list_item(self, list_id, item_id):
        """Remove an item (DELETE /api/shoppinglist/<id>/item/<item_id>)."""
        deleted = shopping_list.remove_item(item_id)
        self._respond_json(200 if deleted else 404, {'success': deleted})

    def handle_add_recipe_to_shopping_list(self, list_id, recipe_id):
        """Add every ingredient of a recipe to a list, merging into existing
        unchecked lines where possible (POST
        /api/shoppinglist/<id>/from-recipe/<recipe_id>, optional body:
        {servings: N} to scale first)."""
        if not shopping_list.get_list(list_id):
            self._respond_json(404, {'success': False, 'error': f'List {list_id} not found'})
            return
        if not db.get_recipe(recipe_id):
            self._respond_json(404, {'success': False, 'error': f'Recipe {recipe_id} not found'})
            return
        content_length = int(self.headers.get('Content-Length', 0))
        data = self._read_json_body() if content_length > 0 else {}
        if data is None:
            return
        count = shopping_list.add_recipe_to_list(list_id, recipe_id, db, servings=data.get('servings'))
        self._respond_json(200, {'success': True, 'items_added': count})

    def handle_add_plan_to_shopping_list(self, list_id, plan_id):
        """Add every recipe in a meal plan to a list (POST
        /api/shoppinglist/<id>/from-plan/<plan_id>)."""
        if not shopping_list.get_list(list_id):
            self._respond_json(404, {'success': False, 'error': f'List {list_id} not found'})
            return
        if not meal_db.get_plan(plan_id):
            self._respond_json(404, {'success': False, 'error': f'Plan {plan_id} not found'})
            return
        count = shopping_list.add_plan_to_list(list_id, plan_id, meal_db, db)
        self._respond_json(200, {'success': True, 'items_added': count})

    def handle_add_pantry_item(self):
        """Manually add (or refresh, if it already exists) a pantry item
        (POST /api/pantry)."""
        data = self._read_json_body()
        if data is None:
            return
        name = (data.get('name') or '').strip()
        if not name:
            self._respond_json(400, {'success': False, 'error': 'name is required'})
            return
        item_id = pantry.add_or_refresh_item(name, quantity=data.get('quantity'), source='manual')
        self._respond_json(201, {'success': True, 'item_id': item_id})

    def handle_confirm_pantry_item(self, item_id):
        """User confirmed they still have this item (PUT /api/pantry/<id>) -
        resets its shelf-life clock."""
        changed = pantry.confirm_item(item_id)
        self._respond_json(200 if changed else 404, {'success': changed})

    def handle_delete_pantry_item(self, item_id):
        """Remove a pantry item, whether because it's used up or the user
        confirmed they don't have it after all (DELETE /api/pantry/<id>)."""
        deleted = pantry.remove_item(item_id)
        self._respond_json(200 if deleted else 404, {'success': deleted})

class ThreadingRecipeServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded so one slow request (an LLM call blocking on an
    unreachable/slow Ollama host - 30-120s timeouts scattered across
    query_planner.py/recipe_adaptation.py/easter_egg.py/etc.) can't stall
    every other concurrent user - a plain TCPServer handles one request at
    a time, so a single hung /craving or /recipe/<id>/adapt request used to
    take the entire app offline until it timed out. daemon_threads=True so
    an in-flight request thread doesn't block clean container shutdown."""
    daemon_threads = True


def run_server(port=8000):
    """Run the recipe server."""
    with ThreadingRecipeServer(("", port), RecipeHandler) as httpd:
        print(f"Server running at http://localhost:{port}/")
        print("Press Ctrl+C to stop the server")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")

if __name__ == "__main__":
    run_server(port=int(os.environ.get("PORT", 8000)))