"""
Local file storage for user-uploaded recipe photos.

server.py deliberately serves zero static files by default (see its
do_GET fallback comment - the inherited SimpleHTTPRequestHandler behavior
would otherwise expose the whole working directory, recipes.db and source
included, to any unmatched path). This module is the one narrow, explicit
exception: an uploads/ directory, filenames generated server-side only
(never derived from client input, so path traversal is structurally
impossible rather than merely filtered), and real magic-byte content
sniffing rather than trusting a client-supplied filename or Content-Type.
"""
import base64
import os
import uuid
import re
from pathlib import Path
from typing import Optional

UPLOADS_DIR = Path(os.environ.get('SOUS_UPLOADS_DIR', 'uploads'))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB

# Server-generated filenames only: 32 lowercase hex chars (uuid4().hex) +
# a fixed, whitelisted extension. Nothing derived from client input is
# ever part of a filename, so this pattern is defense-in-depth, not the
# only thing standing between a request and path traversal.
FILENAME_PATTERN = re.compile(r'^[a-f0-9]{32}\.(jpg|png|gif|webp)$')

_CONTENT_TYPES = {'jpg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}


def _detect_image_type(data: bytes) -> Optional[str]:
    """Sniffs the real file type from magic bytes - never trust a client-
    supplied filename or Content-Type for what's actually in the body."""
    if data[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None


def save_upload(file_base64: str) -> str:
    """Decodes, validates (size + real content sniffing), and saves an
    uploaded image. Returns the generated filename to store in
    recipe_images.filename. Raises ValueError with a user-facing message
    on any validation failure."""
    try:
        data = base64.b64decode(file_base64, validate=True)
    except Exception:
        raise ValueError('file_base64 could not be decoded')

    if len(data) == 0:
        raise ValueError('uploaded file is empty')
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f'file too large ({len(data)} bytes, max {MAX_UPLOAD_BYTES})')

    ext = _detect_image_type(data)
    if ext is None:
        raise ValueError('unrecognized image format - only JPEG, PNG, GIF, and WebP are accepted')

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f'{uuid.uuid4().hex}.{ext}'
    (UPLOADS_DIR / filename).write_bytes(data)
    return filename


def delete_upload(filename: str) -> bool:
    if not FILENAME_PATTERN.match(filename):
        return False
    path = UPLOADS_DIR / filename
    if path.exists():
        path.unlink()
        return True
    return False


def resolve_upload_path(filename: str) -> Optional[Path]:
    """The single choke point the static-serving route uses. Returns the
    file's path only if the filename matches the strict generated-filename
    pattern AND the file exists - anything else (including any attempt at
    '../' or an absolute path) returns None before the filesystem is ever
    touched with untrusted input."""
    if not FILENAME_PATTERN.match(filename):
        return None
    path = UPLOADS_DIR / filename
    if not path.is_file():
        return None
    return path


def content_type_for(filename: str) -> str:
    ext = filename.rsplit('.', 1)[-1]
    return _CONTENT_TYPES.get(ext, 'application/octet-stream')
