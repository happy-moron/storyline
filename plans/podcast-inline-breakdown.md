The existing pipeline generates a separate audio podcast with a couple hosts who expand a dialogue with an "Intro" and a "Breakdown" section, as well as providing an Outro.
They also talk through the vocab.

After the original podcast pipeline was implemented, its flaws start to show:

The hosts are AI-gen and kind of weak - their jokes are poor, etc. 
The main value being provided is in the line-by-line detailed breakdown they give.
The vocab breakdon which is duplicated by the hosts in the "Intro" section is also duplicated by standalone vocab audio gen which does a better job of going through the vocab anyway.

So the only value the "podcast" is really providing is in the grammar point explanation and the breakdown section. (as well as the dialogue repetition)

The breakdown section can be 'inlined' by introducing a line-by-line loop that uses a targeted prompt ("Here's a line, please provide a breakdown of its grammar and vocab") and generates a breakdown of each individual line in a single speaker's voice.