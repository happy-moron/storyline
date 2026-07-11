You are directing a single skilled narrator who is reading a book aloud and voicing all characters themselves. Given the passage at the end of this prompt, split it into narration chunks and give each one a short director's note on tone, pace, and delivery.

Rules:
- Do not add, remove, or reword any text. Every word of the source must appear in exactly one chunk, in original order, punctuation intact.
- Default to LARGE chunks. Plain descriptive or expository narration with a steady tone can and should stay in one chunk — a full paragraph or more. Only start a new chunk when there's a real shift: dialogue starting/ending, a change of emotional register, or a pacing change worth calling out.
- Do not over-coach. If a chunk is just even, neutral narration, leave `@instruct:` blank rather than inventing a direction. Reserve instructions for moments that actually need a specific delivery (a line of dialogue, a tense beat, a joke, a shift in pace). A wall of chunks with a bespoke direction on every one is a failure mode — you are giving notes only where they earn their keep.
- Dialogue tags ("she whispered", "he said") stay attached to the line they punctuate.
- Instructions describe delivery, not casting — since it's one narrator, phrase them as acting notes ("gruff, impatient tone for this line", "quicken pace here"), not character names.

Output format:
- Plain text only. No commentary, headers, or code fences.
- Each chunk is:
  ```
  @instruct: <direction, or blank>
  <chunk text, one or more lines>
  ```
- Separate chunks with exactly one blank line.

Examples:

--- Example 1: dialogue exchange ---
Input:
Mira stared at the door. She had heard something. "Who's there?" she whispered. No answer came. Just the wind, rattling the old glass.

Output:
@instruct:
Mira stared at the door. She had heard something.

@instruct: Frightened whisper
"Who's there?" she whispered.

@instruct:
No answer came. Just the wind, rattling the old glass.

--- Example 2: long descriptive passage kept as one chunk ---
Input:
The valley opened up beneath them, a patchwork of wheat fields turning gold in the late afternoon light. Farmhouses dotted the landscape, their chimneys sending up thin columns of smoke. In the distance, the river caught the sun and threw it back in long, bright ribbons. It was the kind of view that made you forget, for a moment, why you'd come.

Output:
@instruct:
The valley opened up beneath them, a patchwork of wheat fields turning gold in the late afternoon light. Farmhouses dotted the landscape, their chimneys sending up thin columns of smoke. In the distance, the river caught the sun and threw it back in long, bright ribbons. It was the kind of view that made you forget, for a moment, why you'd come.

--- Example 3: steady narration with one embedded beat that earns a note ---
Input:
They walked for another hour without speaking. The path narrowed, then widened again as it crossed an old stone bridge. Halfway across, Tom stopped and grabbed her arm. "Did you hear that?" She hadn't. They stood still, listening, until the birdsong resumed and Tom, embarrassed, let go and kept walking.

Output:
@instruct:
They walked for another hour without speaking. The path narrowed, then widened again as it crossed an old stone bridge.

@instruct: Sudden, alarmed, low volume
Halfway across, Tom stopped and grabbed her arm. "Did you hear that?"

@instruct:
She hadn't. They stood still, listening, until the birdsong resumed and Tom, embarrassed, let go and kept walking.

---
Now process the following text:


