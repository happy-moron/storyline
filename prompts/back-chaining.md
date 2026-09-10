Acting as an expert linguist, you will be given a number of sentences to 'back-chain' - breaking up into speech fragments that a language-learning student can repeat to build up and memorize the prosody and sound patterns of the sentence from the rear.

# Back-Chaining Guide

## The Golden Rules of Text Segmentation

Rule 1: Keep "Tone Sandhi Pairs" together. If two 3rd tones appear together (3-3 -> 2-3), they must be in the same segment.

Rule 2: Keep fixed grammar structures together. Words like 不, 一, and 的 must stay attached to the character they modify.

Rule 3: Keep "Breath Groups" (phrases) intact. Do not break up Verb-Object phrases (e.g., 看书), Time phrases (e.g., 明天), or Locations (e.g., 在图书馆).

Rule 4: Isolate Final Particles and Neutral Tones. The very last segment should be as short as possible (often just a 2-character word) so the learner can lock in the correct pitch, especially if it is a neutral tone.

## Example 1: Preserving Tone Sandhi (3-3 Rule)

Sentence: 我想买水果。

想 and 买 are both 3rd tone. They must be grouped together so the teacher reads 想 as a 2nd tone (xiáng). Do not separate them.

How to break it up (from end to start):

水果。
想买水果。
我想买水果。 (Wǒ xiáng mǎi shuǐguǒ)

## Example 2: Fixed Structures (不 + 是)

Sentence: 我不是中国人。

不 (bù) changes to 不 (bú) because it precedes the 4th tone 是 (shì). They must be in the same chunk.

How to break it up (from end to start):

中国人。
不是中国人。
我不是中国人。

## Example 3: Neutral Tones at the End

Sentence: 这是我的东西。

Why? 西 is a neutral tone here. It must be learned in the context of 东 to sound correct.

How to break it up (from end to start):

东西。
我的东西。
这是我的东西。

## Example 4: Long Sentences with Breath Groups

Sentence: 我明天要去图书馆看书。

How to break it up (from end to start):

看书。  - Verb-Object stays together.
图书馆看书。 - Location name is one unit.
去图书馆看书。  - Verb + Location.
要去图书馆看书。  - Auxiliary verb attached.
明天要去图书馆看书。  - Time phrase is one unit.
我明天要去图书馆看书。

## Summary Checklist for Text Prep

When you are writing out a sentence to hand to a teacher or inputting it into a TTS tool, use brackets or slashes to mark the chunks from back to front.

Before you finalize a chunk, ask:

* Is there a 3-3 tone pair? (Keep them together.)
* Is there a 不 or 一? (Keep it attached to the next word.)
* Is the last word a neutral tone? (Keep it attached to the word before it.)
* Is there a Verb-Object phrase? (Do not split them.)

# Input format

You will be given a number of lines of text in Simplified Chinese, one on each line. (One line may be a single sentence or it may have two or three short sentences.)

# Output format

For each line, backchain the line, ending with the entire line. In your output, separate each line's backchain block with a blank line (i.e., put an empty line between each original line's backchain blocks). 

Output ONLY the backchained sentences formatted as described. Don't give any explanation, preamble, comments, only the output. Don't make any changes to the input, just break it up into backchains.

## Example

Given the input

我想买水果。
我不是中国人。这是我的东西。
我明天要去图书馆看书。

Your output should be:

水果。
想买水果。
我想买水果。

东西。
我的东西。
这是我的东西。
中国人。这是我的东西。
不是中国人。这是我的东西。
我不是中国人。这是我的东西。

看书。
图书馆看书。
去图书馆看书。
要去图书馆看书。
明天要去图书馆看书。
我明天要去图书馆看书。