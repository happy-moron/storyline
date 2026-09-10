Acting as an expert linguist, you previously attempted to back-chain some Chinese sentences but the output had errors. The validation errors, original input, and your previous output are all provided below as input.

Identify the errors and fix the back-chain breakdowns. Re-output ALL back-chained sentences, including both the correct ones and the corrected ones.

# Back-Chaining Guide

## The Golden Rules of Text Segmentation

Rule 1: Keep "Tone Sandhi Pairs" together. If two 3rd tones appear together (3-3 -> 2-3), they must be in the same segment.

Rule 2: Keep fixed grammar structures together. Words like 不, 一, and 的 must stay attached to the character they modify.

Rule 3: Keep "Breath Groups" (phrases) intact. Do not break up Verb-Object phrases (e.g., 看书), Time phrases (e.g., 明天), or Locations (e.g., 在图书馆).

Rule 4: Isolate Final Particles and Neutral Tones. The very last segment should be as short as possible (often just a 2-character word) so the learner can lock in the correct pitch, especially if it is a neutral tone.

## Output format

For each line, backchain the line, ending with the entire line. In your output, separate each line's backchain block with a blank line (i.e., put an empty line between each original line's backchain blocks).

Output ONLY the backchained sentences formatted as described. Don't give any explanation, preamble, comments, only the output. Don't make any changes to the input, just break it up into backchains.