import json
import os
from pathlib import Path


def _title_from_slug(slug: str) -> str:
    """Derive a human-readable title from a directory slug."""
    return slug.replace("-", " ").replace("_", " ").title()


def scan_books(books_dir: str | Path) -> list[dict]:
    """Scan *books_dir* for processed books and return manifest entries.

    A directory is considered a processed book if it contains a
    ``pipe/tokenized/`` subdirectory.
    """
    books_dir = Path(books_dir)
    authors: list[dict] = []

    if not books_dir.is_dir():
        return authors

    for author_path in sorted(books_dir.iterdir()):
        if not author_path.is_dir() or author_path.name.startswith("."):
            continue

        author_books: list[dict] = []
        for book_path in sorted(author_path.iterdir()):
            if not book_path.is_dir() or book_path.name.startswith("."):
                continue

            tokenized_dir = book_path / "pipe" / "tokenized"
            if not tokenized_dir.is_dir():
                continue

            slug = book_path.name
            author_books.append({
                "slug": slug,
                "title": _title_from_slug(slug),
                "prefix": slug,
            })

        if author_books:
            authors.append({
                "author": author_path.name,
                "books": author_books,
            })

    return authors


def update_manifest(books_dir: str | Path) -> None:
    """Regenerate ``books/manifest.json`` from the current directory tree."""
    books_dir = Path(books_dir)
    manifest_path = books_dir / "manifest.json"
    authors = scan_books(books_dir)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"books": authors}, f, ensure_ascii=False, indent=2)