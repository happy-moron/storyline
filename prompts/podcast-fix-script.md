You will be given a podcast episode script that is written in a custom format which should be parsed.

The script is being given to you because it has failed to parse. You'll be given a description of the intended script format and the parser error messages along with the erroneous script.

Identify the errors and fix the script if you can.

# Script Format/Guidelines

## Format

The script is a series of sentences, each preceeded by the language of the sentence and the speaker id in the form (en=<speaker-id>). The speakers are running a language learning podcast so although the main language should always be 'en' it is expected that there are words and phrases within it. There are two speakers with set speaker ids - 'student' for the male host Ryan and 'teacher' for the female host Mei.

There may also be sentences of example dialogue with numeric speaker ids.
These are in chinese (e.g. (zh=2)) and have a line of Chinese text followed by the English translation.

Whitespace between the lines of dialogue are okay, but not between the Chinese text and its translation.

### Example of markup

en=teacher
This is an introduction to the grammar point... blah blah blah

en=student
How does this work in context?
zh=2
你的显示器有多大？
How big is your monitor?


## Parsing constraints

* Make sure the sentences, language and speaker ids are well formed as expected.

# Your Output Guidelines

You should clean up the script and fix any errors in it. You have reasonable license to fix the text content; the existing script is somewhat arbitrary so you can rewrite small pieces of it (e.g. providing a missing line) if necessary, as long as it stays coherent and focused on the same theme. Obviously don't touch the parts which don't have errors.

## Output the fixed script if you can.

If the script has clear errors with straightforward fixes, output the fixed script.
Output ONLY the properly formatted text of the script with no changes or additions except for direct fixes for known/obvious errors.

## Output the word GARBAGE if the script is totally unfixable

If the script has:

* Unclear errors
* Truncated or severely malformed content
* Non-obvious or strange things going on

Output just a single all caps word: GARBAGE
Don't output anything else, no explanation, just the word GARBAGE.

# Script to Fix