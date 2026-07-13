import json
import tempfile
from pathlib import Path

from storyline.book.update_manifest import scan_books, update_manifest, _title_from_slug


class TestTitleFromSlug:
    def test_simple(self):
        assert _title_from_slug("thomas-buys-a-car") == "Thomas Buys A Car"

    def test_underscores(self):
        assert _title_from_slug("stalky_and_co") == "Stalky And Co"

    def test_mixed(self):
        assert _title_from_slug("right-ho-jeeves") == "Right Ho Jeeves"


class TestScanBooks:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert scan_books(tmp) == []

    def test_no_tokenized_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "someauthor" / "somebook").mkdir(parents=True)
            assert scan_books(tmp) == []

    def test_processed_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokenized = Path(tmp) / "tolstoy" / "where-love-is" / "pipe" / "tokenized"
            tokenized.mkdir(parents=True)
            (tokenized / "where-love-is_1.txt").touch()

            authors = scan_books(tmp)
            assert len(authors) == 1
            assert authors[0]["author"] == "tolstoy"
            assert len(authors[0]["books"]) == 1
            book = authors[0]["books"][0]
            assert book["slug"] == "where-love-is"
            assert book["prefix"] == "where-love-is"

    def test_skips_dot_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".hidden_author" / "book" / "pipe" / "tokenized").mkdir(parents=True)
            assert scan_books(tmp) == []

    def test_multiple_authors_and_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ab in [("a1", "b1"), ("a1", "b2"), ("a2", "b3")]:
                tok = Path(tmp) / ab[0] / ab[1] / "pipe" / "tokenized"
                tok.mkdir(parents=True)
                (tok / "dummy_1.txt").touch()

            authors = scan_books(tmp)
            assert len(authors) == 2
            assert {a["author"] for a in authors} == {"a1", "a2"}
            a1 = next(a for a in authors if a["author"] == "a1")
            assert len(a1["books"]) == 2

    def test_skips_dirs_without_tokenized(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "author" / "unprocessed_book").mkdir(parents=True)
            tok = Path(tmp) / "author" / "processed_book" / "pipe" / "tokenized"
            tok.mkdir(parents=True)

            authors = scan_books(tmp)
            assert len(authors) == 1
            assert len(authors[0]["books"]) == 1
            assert authors[0]["books"][0]["slug"] == "processed_book"


class TestUpdateManifest:
    def test_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tok = Path(tmp) / "kipling" / "stalky" / "pipe" / "tokenized"
            tok.mkdir(parents=True)
            (tok / "stalky_1.txt").touch()

            update_manifest(tmp)

            manifest_path = Path(tmp) / "manifest.json"
            assert manifest_path.exists()
            data = json.loads(manifest_path.read_text())
            assert len(data["books"]) == 1
            assert data["books"][0]["author"] == "kipling"
            assert data["books"][0]["books"][0]["slug"] == "stalky"