from recipe_model import Recipe
import llm_client


def _build_prompt(recipe: Recipe) -> str:
    ingredients = "\n".join(f"- {i}" for i in recipe.ingredients[:20])
    instructions = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(recipe.instructions[:15]))
    return (
        "Write a short, genuinely funny mock recipe that riffs on this real one. "
        "Keep the dish's core identity recognizable but push it into absurd, "
        "over-the-top comedic territory (exaggerated claims, ridiculous optional "
        "steps, a deadpan chef's-note aside, etc.). Plain text only, no markdown "
        "headers, under 200 words.\n\n"
        f"Real recipe: {recipe.title}\n\nIngredients:\n{ingredients}\n\n"
        f"Instructions:\n{instructions}\n"
    )


def generate_easter_egg_recipe(recipe: Recipe, timeout: int = 90) -> str:
    """Ask the active LLM provider (llm_client.py) to riff comedically on a
    real recipe already in the collection. Not saved to the DB - generated
    fresh on each request. Raises RuntimeError on any failure/timeout
    rather than silently returning something misleading.

    Previously had its own independent SOUS_EASTER_EGG_MODEL env var,
    separate from every other LLM-backed feature's OLLAMA_HOST - now uses
    the same shared provider/model preference as the rest of the app
    (see PLAN.md Phase 17), a deliberate behavior change: one provider
    setting for the whole app, not a per-feature override."""
    try:
        return llm_client.chat(
            [{'role': 'user', 'content': _build_prompt(recipe)}],
            timeout=timeout,
            max_tokens=600,
        )
    except llm_client.LLMUnavailableError as e:
        raise RuntimeError(f"Easter-egg generation unavailable: {e}")
