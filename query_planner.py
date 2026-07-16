from flavor_taxonomy import FLAVOR_TAXONOMY
import llm_client

VALID_FLAVORS = frozenset(name for name, _, _ in FLAVOR_TAXONOMY)


def _build_prompt(user_text: str) -> str:
    categories = ", ".join(sorted(VALID_FLAVORS))
    return (
        f"Valid flavor categories: {categories}.\n\n"
        "A user typed this free-text description of what they want to eat:\n"
        f'"{user_text}"\n\n'
        "Translate it into a JSON object with these fields:\n"
        '- "flavors": array of 0-5 flavor category strings from the valid list above that '
        "best capture the mood/craving (lowercase, exact spelling). E.g. \"comforting\" "
        "tends toward fatty_rich/warm_spice/umami; \"light and fresh\" tends toward "
        "fresh_green/citrus/herbal. Empty array if nothing maps clearly.\n"
        '- "cuisine": a single cuisine name if one is clearly implied (e.g. "italian", '
        '"mexican"), else null.\n'
        '- "max_total_time_minutes": an integer if the user implies a time constraint '
        '(e.g. "quick", "weeknight" -> 30; "no rush" -> null), else null.\n'
        '- "keywords": array of 0-5 plain-English search keywords capturing anything '
        "concrete (dish names, named ingredients, meal type) not already captured by the "
        "fields above, for a fallback keyword search.\n\n"
        "Respond with ONLY the JSON object, no other text."
    )


def plan_intent_query(user_text: str, timeout: int = 30) -> dict:
    """Translate a free-text mood/intent query into structured search filters via the
    active LLM provider (llm_client.py). On any failure (provider unreachable, bad JSON,
    etc.) falls back to a keyword-only plan built from the raw input, so intent search
    degrades to plain keyword search rather than failing outright."""
    fallback = {'flavors': [], 'cuisine': None, 'max_total_time_minutes': None, 'keywords': [user_text]}

    try:
        parsed = llm_client.chat_json(
            [{"role": "user", "content": _build_prompt(user_text)}],
            timeout=timeout,
        )
    except llm_client.LLMUnavailableError as e:
        print(f"Error planning intent query: {e}")
        return fallback

    flavors = [f for f in parsed.get('flavors') or [] if f in VALID_FLAVORS][:5]
    cuisine = parsed.get('cuisine')
    cuisine = cuisine.strip().lower() if isinstance(cuisine, str) and cuisine.strip() else None
    max_time = parsed.get('max_total_time_minutes')
    max_time = max_time if isinstance(max_time, int) and max_time > 0 else None
    keywords = [k for k in (parsed.get('keywords') or []) if isinstance(k, str) and k.strip()][:5]

    return {
        'flavors': flavors,
        'cuisine': cuisine,
        'max_total_time_minutes': max_time,
        'keywords': keywords,
    }
