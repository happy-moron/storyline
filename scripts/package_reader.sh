#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

# ─── package_reader.sh ────────────────────────────────────────────────
# Packages the Chinese Reader app into a tar.gz archive suitable for
# copying to a web root.  Only runtime-essential files are included:
#
#   Core reader files   index.html, styles.css, flashcards.html, flashcards.css
#   Dictionary          dict/custom_dict.json
#   Book manifest       books/manifest.json
#   Flashcard manifest  books/flashcard-manifest.json
#   Vocab store         books/vocab/{audio,images,texts}/
#
# For each book listed in the manifest:
#   books/{author}/{slug}/pipe/source/{prefix}_{N}.txt
#   books/{author}/{slug}/pipe/tokenized/{prefix}_{N}.txt
#   books/{author}/{slug}/chunks/{prefix}_{N}.json          (if present)
#   books/{author}/{slug}/audio/{audioFile}                 (referenced by chunk JSONs)
#
#   books/{author}/{slug}/flashcards/cards.pdf              (per-episode; audio/images from shared store)
#
# Intermediate pipeline files (*_raw.txt, *_numbered.txt,
# *_input.txt, split/, etc.) are EXCLUDED.
# ────────────────────────────────────────────────────────────────────────

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

OUTDIR="${1:-dist}"
OUTFILE="${OUTDIR}/storyline-reader.tar.gz"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Staging in $STAGE"

# ── 1. Core reader files ──────────────────────────────────────────────
cp index.html styles.css flashcards.html flashcards.css "$STAGE/"

# ── 2. Dictionary ─────────────────────────────────────────────────────
mkdir -p "$STAGE/dict"
cp dict/custom_dict.json "$STAGE/dict/"

# ── 3. Manifests ──────────────────────────────────────────────────────
mkdir -p "$STAGE/books"
cp books/manifest.json "$STAGE/books/manifest.json"
if [ -f books/flashcard-manifest.json ]; then
    cp books/flashcard-manifest.json "$STAGE/books/"
fi

# ── 4. Vocab store (shared deduplicated assets) ───────────────────────
if [ -d books/vocab ]; then
    echo "==> Copying vocab store..."
    cp -r books/vocab "$STAGE/books/vocab"
fi

# ── 5. Per-book runtime files ─────────────────────────────────────────
load_book_data() {
    local author="$1" slug="$2" prefix="$3"
    local bookdir="$STAGE/books/${author}/${slug}"

    # pipe/source/ and pipe/tokenized/ – only {prefix}_{N}.txt files
    local src="$PROJECT_ROOT/books/${author}/${slug}/pipe/source"
    if [ -d "$src" ]; then
        mkdir -p "$bookdir/pipe/source"
        for f in "$src/${prefix}"_*.txt; do
            [ -f "$f" ] || continue
            local base; base="$(basename "$f")"
            # skip *_input.txt files (pipeline inputs, not runtime)
            case "$base" in
                *_input.txt) continue ;;
            esac
            cp "$f" "$bookdir/pipe/source/"
        done
    fi

    local tok="$PROJECT_ROOT/books/${author}/${slug}/pipe/tokenized"
    if [ -d "$tok" ]; then
        mkdir -p "$bookdir/pipe/tokenized"
        for f in "$tok/${prefix}"_*.txt; do
            [ -f "$f" ] || continue
            local base; base="$(basename "$f")"
            case "$base" in
                *_input.txt) continue ;;
            esac
            cp "$f" "$bookdir/pipe/tokenized/"
        done
    fi

    # chunks/ – only {prefix}_{N}.json files (no _raw.txt, _numbered.txt)
    local chk="$PROJECT_ROOT/books/${author}/${slug}/chunks"
    if [ -d "$chk" ]; then
        mkdir -p "$bookdir/chunks"
        for f in "$chk/${prefix}"_*.json; do
            [ -f "$f" ] || continue
            cp "$f" "$bookdir/chunks/"
        done
    fi

    # audio/ – discover files from chunk JSONs
    local aud="$PROJECT_ROOT/books/${author}/${slug}/audio"
    if [ -d "$aud" ] && [ -d "$bookdir/chunks" ]; then
        mkdir -p "$bookdir/audio"
        # collect all audio_zh / audio_en references from every chunk JSON
        for jsonf in "$bookdir/chunks/${prefix}"_*.json; do
            [ -f "$jsonf" ] || continue
            while IFS= read -r line; do
                # extract quoted filename from "audio_zh": "..." or "audio_en": "..."
                if [[ "$line" =~ \"audio_(zh|en)\":[[:space:]]*\"([^\"]+)\" ]]; then
                    local audiofile="${BASH_REMATCH[2]}"
                    local srcfile="$aud/$audiofile"
                    if [ -f "$srcfile" ]; then
                        cp "$srcfile" "$bookdir/audio/"
                    else
                        echo "  [warn] audio file not found: $srcfile" >&2
                    fi
                fi
            done < "$jsonf"
        done
    fi

    # flashcards/ – per-episode PDF only; audio/images now served from shared vocab store
    local fc="$PROJECT_ROOT/books/${author}/${slug}/flashcards"
    if [ -d "$fc" ]; then
        mkdir -p "$bookdir/flashcards"
        # PDF is per-episode, everything else comes from shared vocab store
        for pdf in "$fc"/cards.pdf; do
            if [ -f "$pdf" ]; then
                cp "$pdf" "$bookdir/flashcards/"
            fi
        done
    fi
}

# Parse manifest and process each book
echo "==> Processing books from manifest..."
MANIFEST="books/manifest.json"
if [ ! -f "$MANIFEST" ]; then
    echo "ERROR: $MANIFEST not found – cannot determine books to package." >&2
    exit 1
fi

# Use python to parse the manifest robustly (avoids fragile jq-less json parsing)
python3 -c "
import json, sys, os

with open('$MANIFEST') as f:
    manifest = json.load(f)

for entry in manifest.get('books', []):
    author = entry.get('author', '')
    for book in entry.get('books', []):
        slug = book.get('slug', '')
        prefix = book.get('prefix', '')
        print(f'{author}|{slug}|{prefix}')
" | while IFS='|' read -r author slug prefix; do
    echo "  -> $author / $slug"
    load_book_data "$author" "$slug" "$prefix"
done

# ── 6. Create tarball ─────────────────────────────────────────────────
echo "==> Creating $OUTFILE ..."
mkdir -p "$OUTDIR"
tar -C "$STAGE" -czf "$OUTFILE" .

echo "==> Done: $(du -sh "$OUTFILE" | cut -f1)  $(ls -lh "$OUTFILE" | awk '{print $5}')"
echo "    Extract with: tar -xzf $OUTFILE"