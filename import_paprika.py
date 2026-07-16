import zipfile
import gzip
import json
import re
import sys
from pathlib import Path

from recipe_model import RecipeDatabase, Recipe
from categories import add_category
import uploads
import recipe_images


def parse_time_to_minutes(time_str: str) -> int:
    """Parses free text like '15 min', '1 hour', '1 hour 30 min', '90 minutes' into total minutes as an int."""
    if not time_str:
        return 0
    
    # Find hours and minutes using regex
    hours_match = re.search(r'(\d+)\s*(?:hour|hr)', time_str, re.IGNORECASE)
    minutes_match = re.search(r'(\d+)\s*min', time_str, re.IGNORECASE)
    
    total_minutes = 0
    
    if hours_match:
        total_minutes += int(hours_match.group(1)) * 60
        
    if minutes_match:
        total_minutes += int(minutes_match.group(1))
        
    return total_minutes


def parse_paprikarecipes_file(file_path: str) -> list:
    """Opens the zip file at file_path and returns a list of raw recipe dicts."""
    recipes = []
    
    with zipfile.ZipFile(file_path, 'r') as zip_file:
        for entry in zip_file.namelist():
            if entry.endswith('.paprikarecipe'):
                # Read and decompress the gzip content
                compressed_data = zip_file.read(entry)
                decompressed_data = gzip.decompress(compressed_data)
                recipe_dict = json.loads(decompressed_data.decode('utf-8'))
                recipes.append(recipe_dict)
                
    return recipes


def import_paprika_file(file_path: str, db_path: str = 'recipes.db') -> list:
    """Import recipes from a Paprika .paprikarecipes file."""
    raw_recipes = parse_paprikarecipes_file(file_path)
    
    recipe_ids = []
    
    # Create database connection
    db = RecipeDatabase(db_path=db_path)
    
    for recipe_data in raw_recipes:
        # Parse ingredients - split on newlines, strip and filter empty lines
        ingredients = [line.strip() for line in recipe_data['ingredients'].split('\n') if line.strip()]
        
        # Parse directions - split on double newlines, strip and filter empty steps
        instructions = [step.strip() for step in recipe_data['directions'].split('\n\n') if step.strip()]
        
        # Parse prep_time and cook_time to minutes
        prep_time = parse_time_to_minutes(recipe_data.get('prep_time', ''))
        cook_time = parse_time_to_minutes(recipe_data.get('cook_time', ''))
        
        # Parse servings - get first sequence of digits, default to 1 if none found
        servings_match = re.search(r'(\d+)', recipe_data.get('servings', ''))
        servings = int(servings_match.group(1)) if servings_match else 1
        
        # Create Recipe object
        recipe = Recipe(
            title=recipe_data['name'],
            description='',
            ingredients=ingredients,
            instructions=instructions,
            prep_time=prep_time,
            cook_time=cook_time,
            servings=servings,
            url=recipe_data.get('source_url', ''),
            license='user-imported'
        )
        
        # Save recipe to database
        recipe_id = db.save_recipe(recipe)
        recipe_ids.append(recipe_id)

        # Add categories
        for category in recipe_data.get('categories', []):
            add_category(recipe_id, category, db_path=db_path)

        # Photo: prefer the embedded photo_data (base64 JPEG Paprika stores
        # for locally-attached photos - higher quality, doesn't rot like an
        # external link) over image_url (only set when the recipe was
        # originally imported into Paprika from a website). Best-effort -
        # skip silently on a corrupt/unrecognized embedded photo rather than
        # failing the whole recipe's import over one bad image.
        photo_data = recipe_data.get('photo_data')
        image_url = recipe_data.get('image_url')
        if photo_data:
            try:
                filename = uploads.save_upload(photo_data)
                recipe_images.add_image_upload(recipe_id, filename, db_path=db_path)
            except ValueError:
                pass
        elif image_url:
            recipe_images.add_image_url(recipe_id, image_url, db_path=db_path)
    
    return recipe_ids


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 import_paprika.py <paprikarecipes_file>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    recipe_ids = import_paprika_file(file_path)
    print('IDS:', recipe_ids)