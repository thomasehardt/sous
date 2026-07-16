import json
from import_url_recipe import extract_recipe_data
from recipe_model import RecipeDatabase, Recipe
from categories import add_category

def as_text(value):
    if isinstance(value, list):
        return ', '.join(str(v) for v in value)
    elif not value:
        return ''
    else:
        return value

def as_list(value):
    if isinstance(value, list):
        return [str(v) for v in value]
    elif value:
        return [str(value)]
    else:
        return []

def import_bulk_file(file_path, db_path='recipes.db'):
    with open(file_path, 'r') as f:
        recipe_json_list = json.load(f)
    
    # If the loaded JSON is a single dict instead of a list, wrap it in a list
    if not isinstance(recipe_json_list, list):
        recipe_json_list = [recipe_json_list]
    
    db = RecipeDatabase(db_path=db_path)
    recipe_ids = []
    
    for recipe_json in recipe_json_list:
        recipe_data = extract_recipe_data(recipe_json)
        
        recipe = Recipe(
            title=recipe_data['title'],
            description=recipe_data['description'],
            ingredients=recipe_data['ingredients'],
            instructions=recipe_data['instructions'],
            servings=recipe_data['servings'] if isinstance(recipe_data['servings'], int) else 1,
            cuisine=as_text(recipe_data['cuisine']),
            difficulty=as_text(recipe_data['difficulty']),
            url=recipe_json.get('url', '') or '',
            license='user-imported'
        )
        
        recipe_id = db.save_recipe(recipe)
        recipe_ids.append(recipe_id)
        
        for category in as_list(recipe_json.get('recipeCategory', [])):
            add_category(recipe_id, category, db_path=db_path)
    
    return recipe_ids

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 import_bulk.py <file_path>")
        sys.exit(1)
    
    recipe_ids = import_bulk_file(sys.argv[1])
    print('IDS:', recipe_ids)