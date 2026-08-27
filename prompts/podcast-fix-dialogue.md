You will be given a podcast episode script that is written in a custom format which should be parsed.

The script is being given to you because it has failed to parse. You'll be given a description of the intended script format and the parser error messages along with the erroneous script.

Identify what the error message is and fix the script if you can.

# Script Format/Guidelines

The script is a series of sentences, each preceeded by the language of the sentence (zh) and the speaker id. 

The speaker ids are just numbers which are used to identify the speakers.

In the dialogue, each line of Chinese must have its English translation provided on a new line directly beneath it.

### Example of dialogue markup

zh=1
我可以对你直言不讳吗？
Can I speak honestly with you?
zh=2
没关系，先生。
That is okay, sir

# Your Output Guidelines

You should clean up the dialogue and fix any errors in it. You have reasonable license to fix the text content; the existing dialogue is somewhat arbitrary so you can rewrite small pieces of it if necessary, as long as it stays coherent and based on the same theme. Obviously don't touch the parts which don't have errors.

## Output the fixed script if you can.

If the script has clear errors with straightforward fixes, output the fixed script.
Output ONLY the properly formatted text of the script with no changes or additions except for direct fixes for known/obvious errors.

## Output the word GARBAGE if the script is unfixable

If the script has:

* Unclear errors
* Truncated or severely malformed content
* Non-obvious or strange things going on

Output just a single all caps word: GARBAGE
Don't output anything else, no explanation, just the word GARBAGE.

# Script to Fix