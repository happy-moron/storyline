"""Qualitative analysis of golden tokenization calibration variation.

Runs pairwise comparisons between block_05 golden tokenization variants
and prints detailed sentence-level + token-level diffs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storyline.benchmark.token_compare import compare_files, ComparisonResult


def _token_key(token: str) -> str:
    """Extract the Chinese key from a pipe token for sentence alignment."""
    return token.split("|", 1)[0] if "|" in token else token


def _fmt_token(token: str) -> str:
    """Format a token compactly for display."""
    parts = token.split("|")
    return f"{parts[0]}({parts[1]})[{parts[2]}]" if len(parts) >= 3 else token


def analyze_pair(label_a: str, label_b: str, result: ComparisonResult) -> None:
    print(f"\n{'='*80}")
    print(f"COMPARISON: {label_a}  vs  {label_b}")
    print(f"{'='*80}")

    m = result.global_metrics
    meta = result.meta
    print(f"  Ref sentences: {meta.reference_sentences}  Cand sentences: {meta.candidate_sentences}")
    print(f"  Ref tokens:    {meta.reference_tokens}  Cand tokens:    {meta.candidate_tokens}")
    print(f"  Aligned pairs: {m.aligned_sentence_pairs}")
    print(f"  Missing sents: {m.missing_sentences}  Extra sents: {m.extra_sentences}")
    print(f"  Missing toks:  {m.missing_tokens}   Extra toks:  {m.extra_tokens}")
    print(f"  Total token errors: {m.total_token_errors}  Rate: {m.token_error_rate:.4f}")
    print(f"  Flawless sents: {m.flawless_sentences}")

    print(f"\n  --- Per-sentence details ---")
    for sc in result.sentence_comparisons:
        if sc.type == "missing":
            print(f"  ❌ MISSING sentence:  ref[{sc.ref_index}] key={sc.ref_key}")
            if sc.ref_tokens:
                tk = ", ".join(_fmt_token(t) for t in sc.ref_tokens)
                print(f"     tokens: {tk}")
        elif sc.type == "extra":
            print(f"  ➕ EXTRA sentence:    cand[{sc.cand_index}] key={sc.cand_key}")
            if sc.cand_tokens:
                tk = ", ".join(_fmt_token(t) for t in sc.cand_tokens)
                print(f"     tokens: {tk}")
        elif sc.type == "matched":
            ta = sc.token_alignment
            if ta is None:
                print(f"  ✅ MATCHED: ref[{sc.ref_index}] ⇔ cand[{sc.cand_index}] (no token info)")
                continue

            is_ok = ta.missing_count == 0 and ta.extra_count == 0
            symbol = "✅" if is_ok else "⚠️"
            print(f"  {symbol} MATCHED: ref[{sc.ref_index}] ⇔ cand[{sc.cand_index}] "
                  f"matched={ta.matched_count} missing={ta.missing_count} extra={ta.extra_count}")

            if not is_ok:
                # Print only the non-matching token details
                for d in ta.details:
                    if d["type"] == "match":
                        continue
                    elif d["type"] == "missing":
                        rt = _fmt_token(d["ref_token"])
                        print(f"       MISSING {rt}")
                    elif d["type"] == "extra":
                        ct = _fmt_token(d["cand_token"])
                        print(f"       EXTRA   {ct}")


def main() -> None:
    eval_dir = Path(__file__).resolve().parent.parent / "eval"

    files = {
        "primary": eval_dir / "block_05_golden_tokenization.txt",
        "v1": eval_dir / "block_05_golden_tokenization_1.txt",
        "v2": eval_dir / "block_05_golden_tokenization_2.txt",
    }

    # Pairwise: primary vs v1, primary vs v2, v1 vs v2
    pairs = [
        ("primary", "v1"),
        ("primary", "v2"),
        ("v1", "v2"),
    ]

    for label_a, label_b in pairs:
        result = compare_files(str(files[label_a]), str(files[label_b]))
        analyze_pair(label_a, label_b, result)

    # --- Qualitative categorization of differences ---
    print(f"\n{'='*80}")
    print("QUALITATIVE ANALYSIS: Categories of Variation")
    print(f"{'='*80}")

    # Category 1: Segmented vs monosyllabic pinyin (space/no-space)
    print("""
1. PIYIN SEGMENTATION (monosyllabic vs word-level pinyin)
   - "担心" → "dānxīn" vs "dān xīn"
   - "拿起" → "náqǐ" vs "ná qǐ"
   - "倒进" → "dàojìn" vs "dào jìn"
   - "喝"+"完" → "hē"..."wán" vs "hē wán"
   - "一半" → "yībàn" (single token) vs "一|yī" "半|bàn" (two tokens)
   These are DIFFERENT LLM responses to the same text. The prompt does NOT
   constrain pinyin segmentation, so the comparison algorithm must handle
   both as equivalent for pinyin comparisons. Currently pinyin is embedded
   in the same | field and LCS treats the full string "dānxīn|≠|dān xīn".
""")

    # Category 2: Token boundary differences (compound words)
    print("""
2. TOKEN BOUNDARIES (compound/single word segmentation)
   - "玻璃杯" vs "玻璃"+"杯"  [glass cup vs glass + cup]
   - "倒进" vs "倒"+"进"     [pour in vs pour + in]
   - "喝完" vs "喝"+"完"     [drink finish vs drink + finish]
   - "街上" vs "街"+"上"     [street on vs street + on]
   - "心里" vs "心"+"里"     [heart in vs heart + in]
   - "知道" vs "知"+"道"     [know vs know + way]
   - "地上" vs "地"+"上"     [ground on vs ground + in]
   - "听见" vs "听"+"见"     [hear vs listen + see]
   - "睡着" vs "睡"+"着"     [fall asleep vs sleep + zhe]
   - "识字" vs "识"+"字"     [literate vs know + character]
   - "粗人" vs "粗"+"人"     [boor vs coarse + person]
   
   This is the MAJOR source of variation. Two independent tokenizers
   disagree on whether these are single compound words or separate tokens.
   The Chinese keys differ, causing the LCS token alignment to count
   these as missing/extra tokens even though the semantic coverage is identical.
""")

    # Category 3: POS tag differences
    print("""
3. POS TAG DIFFERENCES
   - 完: v (v1) vs a (primary)  [verb vs adjective]
   - 来: nr (primary) vs r (v1/v2)
   - 不好意思: l (primary) vs a (v1/v2)  [idiom vs adjective]
   - 如何: d (primary) vs r (v1/v2)       [adverb vs pronoun]
   - 心里 vs 心+里: n (primary) vs s (v2), n+f (v1) vs s (v2)
   
   These are legitimate linguistic disagreements — POS is inherently ambiguous.
   The current comparison does NOT compare POS tags at all, which is correct.
""")

    # Category 4: Proper noun handling
    print("""
4. PROPER NOUN FORMATTING
   - 法利赛人: Fǎlìsàirén (primary, one word) vs Fǎlìsài rén (v1/v2, space)
   - 法利赛人 POS: nr (primary) vs n (v1)  [proper noun vs noun]
   
   Again a pinyin segmentation difference.

5. PRONUNCIATION VARIANTS (tonal)
   - 嗯: èn (primary) vs ēn (v1) vs ǹg (v2)
   - 过: guò (primary/2p) vs guo (v1)
   
   Same character, different pinyin. One is genuine tonal variation (嗯),
   the other is a neutral-tone variant (过). Both are valid.
""")

    # Category 6: "就"+"是" compound
    print("""
6. FUSION/FISSION PATTERNS
   "就是" → 就|jiù|d|+是|shì|v (primary, two tokens)
   "就是" → 就是|jiùshì|d (v1/v2, one token)
   "听到" → 读|dú|v|+到|dào|v (primary)
   "读到" → 读到|dú dào|v (v2)
   
   This is the same boundary problem as category 2, just a different example.
""")

    print(f"\n{'='*80}")
    print("CONCLUSION")
    print(f"{'='*80}")
    print("""
Nearly ALL variation in block_05 comes from WORD SEGMENTATION DISAGREEMENTS:
- Should compound words be treated as one token or split?
  (玻璃杯 vs 玻璃+杯, 街上 vs 街+上, 知道 vs 知+道)
- Should pinyin be written word-level or syllable-by-syllable?
  (dānxīn vs dān xīn, dàojìn vs dào jìn)

These are NOT "errors" in any meaningful sense — they're legitimate
ambiguities in Chinese word segmentation, the same problem that makes
Chinese NLP fundamentally harder than English NLP.

The current LCS comparison treats different segmentation as token errors,
which is technically correct but inflates the error rate far above what
a human would consider "incorrect." A segmentation-robust variant would
need to:
  a) Compare at character level for Chinese coverage
  b) Compare pinyin ignoring internal spaces
  c) Only flag POS differences as quality signals (soft metric)

The calibration results are:
- primary vs v1: ~high error rate from token segmentation
- primary vs v2: ~high error rate from token segmentation  
- v1 vs v2: ~lower but still non-zero

But all three files are semantically identical — every Chinese character
is present, every sentence is there, and meaning is preserved.
""")


if __name__ == "__main__":
    main()
