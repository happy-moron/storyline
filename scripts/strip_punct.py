#!/usr/bin/env python3
import sys
import unicodedata

def strip_punctuation(text: str) -> str:
    """Replace all Unicode punctuation with spaces, then collapse whitespace."""
    result = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith('P'):
            result.append(' ')
        else:
            result.append(ch)
    return ' '.join(''.join(result).split())


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        for line in f:
            print(strip_punctuation(line))


if __name__ == '__main__':
    main()
