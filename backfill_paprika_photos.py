"""One-time backfill of photos for the 555 recipes imported from the
household's real Paprika export (2026-07-09/2026-07-16, both the same
555-recipe library). import_paprika.py silently dropped every recipe's
photo_data/image_url until this same session's fix - this recovers them
by re-reading the original export file and matching each entry back to
its already-imported recipes row.

Matched by (title, url) - source_url is stored verbatim as recipes.url on
import, so this is an exact join key, not fuzzy matching. Verified 1:1
clean (555/555 match, no orphans on either side) before writing this.
Idempotent: skips any matched recipe that already has an image_url, so
re-running after a partial run or after new manual photos won't
duplicate/overwrite anything.
"""
import gzip
import json
import sqlite3
import sys
import zipfile

import recipe_images
import uploads


def backfill_paprika_photos(file_path: str, db_path: str = 'recipes.db') -> dict:
    conn = sqlite3.connect(db_path)
    stats = {'matched': 0, 'unmatched': [], 'added_photo': 0, 'added_url': 0, 'skipped_had_image': 0, 'no_photo_in_source': 0}

    with zipfile.ZipFile(file_path) as z:
        for entry in z.namelist():
            if not entry.endswith('.paprikarecipe'):
                continue
            recipe_data = json.loads(gzip.decompress(z.read(entry)))
            title = recipe_data.get('name', '')
            source_url = recipe_data.get('source_url', '') or ''

            row = conn.execute(
                "SELECT id, image_url FROM recipes WHERE title = ? AND url = ? AND license = 'user-imported'",
                (title, source_url),
            ).fetchone()
            if row is None:
                stats['unmatched'].append(title)
                continue
            recipe_id, existing_image = row
            stats['matched'] += 1
            if existing_image:
                stats['skipped_had_image'] += 1
                continue

            photo_data = recipe_data.get('photo_data')
            image_url = recipe_data.get('image_url')
            if photo_data:
                try:
                    filename = uploads.save_upload(photo_data)
                    recipe_images.add_image_upload(recipe_id, filename, db_path=db_path)
                    stats['added_photo'] += 1
                except ValueError:
                    if image_url:
                        recipe_images.add_image_url(recipe_id, image_url, db_path=db_path)
                        stats['added_url'] += 1
                    else:
                        stats['no_photo_in_source'] += 1
            elif image_url:
                recipe_images.add_image_url(recipe_id, image_url, db_path=db_path)
                stats['added_url'] += 1
            else:
                stats['no_photo_in_source'] += 1

    conn.close()
    return stats


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 backfill_paprika_photos.py <paprikarecipes_file>')
        sys.exit(1)
    result = backfill_paprika_photos(sys.argv[1])
    print(json.dumps(result, indent=2))
