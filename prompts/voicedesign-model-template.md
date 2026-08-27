- **Be objective, not evaluative:** "deep, crisp, fast-paced" works; "nice voice" or "sounds trustworthy" does not — those describe listener reaction, not the voice itself.

---

## SECTION 1 — Character Identity *(optional)*

**Purpose:** A label to route this prompt to the right character in the pipeline.
**Form:** name or role tag only — never sent to the model as part of `instruct`.

---

## SECTION 2 — Dimension Worksheet *(scratch layer, not sent to the model)*

Fill these seven core fields first, in isolation, before composing the final prompt. This ensures every dimension gets deliberate consideration. For a Rich-register prompt (see Section 3), add optional extra rows as needed — Delivery Arc, Accent (bonus, not guaranteed effective), Timbre detail — since the longer format has room for them.

| Dimension | What Works | Your Entry |
|---|---|---|
| **Gender** | Male / Female / Neutral | |
| **Age** | A specific age, or a range: child 5–12, teenager, young adult 19–35, middle-aged 36–55, elderly 55+ | |
| **Pitch** | High / Medium / Low | |
| **Pace** | Fast / Medium / Slow | |
| **Emotion** | Cheerful, calm, gentle, serious, lively, composed, soothing — a single baseline, or a start→end shift if the character genuinely has one | |
| **Characteristics** | Magnetic, crisp, hoarse, mellow, sweet, rich, powerful — the timbre/texture of the voice itself | |
| **Use case** | News broadcast, audiobook, animation, documentary narration, etc. | |
| *(optional, Rich only)* **Delivery Arc** | How the voice moves over the course of a scene, if it genuinely does — e.g. "starts commanding, settles into wry amusement" | |
| *(optional, Rich only)* **Accent** | Bonus descriptor, not a guaranteed control — e.g. Standard Mandarin, British English | |

---

## SECTION 3 — Instruct Prompt *(the actual deliverable)*

**Purpose:** This is the exact text sent to the model's `instruct` parameter. Choose one of two registers depending on the character's importance and complexity — both are attested in official examples.

**Concise register** — for minor or single-scene characters:
- 1–3 sentences, roughly 15–40 words. Weave 4–6 worksheet dimensions into flowing prose rather than concatenating them.
- End with (or otherwise include) the use case — it tells the model the *register* to aim for, not just raw voice qualities.

**Rich register** — for major or recurring characters, up to the 2,048-character ceiling:
- Either extended flowing prose *or* a structured field list (`Gender: Male. Pitch: low, stable. Pace: ...`) — both forms appear in official examples; use whichever reads more clearly given how much you're specifying.
- Free to include a Delivery Arc, bonus Accent descriptor, and finer-grained Timbre/Characteristics language than the concise register has room for.
- Still governed by the same five principles: specific, multi-dimensional, objective, original, concise (concise here means "no wasted words," not "short").

Re-read either register against the five official principles before finalizing: **specific** (concrete adjectives, not "nice"/"good"), **multi-dimensional**, **objective** (physical qualities, not listener reaction), **original** (no celebrity/character-brand comparisons), **concise** (every word earns its place, at whatever length).

---

## SECTION 4 — Character Context *(optional, kept outside the instruct prompt)*

**Purpose:** For recurring characters in a long audiobook, this is reference material an LLM uses to *derive* consistent Section 2/3 choices across many design calls. For a Concise-register prompt it stays external scaffolding, never sent to the model. For a Rich-register prompt, some of this material may be folded directly into the instruct text itself (as in the original official examples, which include background and personality alongside pure vocal description) — in that case it's no longer just scaffolding, it's part of the deliverable.
**Form:** 2–4 sentences covering role in the story and the traits that most plausibly shape how they'd sound (age, temperament, social bearing).

---

## Example A — Concise register (minor character)

**Character Name:** Boy Narrator (adventure serial, single-scene walk-on)

**Dimension Worksheet**

| Dimension | Entry |
|---|---|
| Gender | Male |
| Age | Teenager (13–15) |
| Pitch | Medium-high |
| Pace | Medium, quickening |
| Emotion | Lively |
| Characteristics | Crisp, bright |
| Use case | Audiobook narration |

**Instruct Prompt:**
"A lively teenage male voice, medium-high pitch, crisp and bright, with a medium pace that quickens with excitement. Suitable for first-person audiobook narration of an adventure story."

*(27 words — well past the "too broad" floor, deliberately short because this character doesn't need more.)*

**Character Context:** A boy narrator recounting his own adventure after the fact — earnest, still boyish, growing more self-possessed as events unfold. Concise is the right call here: he's a single-register presence for this use, so there's little to gain from spending more of the character budget on him.

---

## Example B — Rich register (major, recurring character)

**Character Name:** Long John Silver (recurring antagonist, full novel)

**Dimension Worksheet**

| Dimension | Entry |
|---|---|
| Gender | Male |
| Age | 50s |
| Pitch | Low, warm, capable of a hard flat edge |
| Pace | Easy and unhurried in ordinary talk, clipped and fast when commanding |
| Emotion | Outwardly jovial and composed almost always; cold calculation underneath, surfacing instantly when useful |
| Characteristics | Rich, gravelly, magnetic, weathered |
| Use case | Audiobook — recurring antagonist across a full novel |
| Delivery Arc | Genial storytelling voice that can snap, without warning, into a flat, quiet, menacing register |
| Accent | West Country English sailor's dialect (bonus descriptor) |

**Instruct Prompt (field-list form, matching the original official examples):**
"Gender: Male. Age: 50s. Pitch: low, warm, rolling, with a sudden hard flat edge when threatening. Pace: easy and unhurried in ordinary talk, clipped and fast under command. Volume: hearty and carrying in good humor, dropping to a low dangerous quiet when menacing. Characteristics: rich, gravelly, magnetic, sea-weathered. Emotion: outwardly jovial and composed almost constantly, with a cold, watchful calculation that can surface instantly. Accent: West Country English sailor's dialect. Use case: audiobook narration of a charismatic, dangerous, recurring antagonist across a full novel."

*(~85 words / well under the 2,048-character ceiling — deliberately long because this character needs a stable, richly specified voice across an entire book, and the arc between "jovial" and "menacing" is core to who he is.)*

**Character Context:** The ship's cook, secretly the ringleader of a mutiny — charms the crew and a young boy passenger alike while calculating his own advantage at every turn. The Rich register earns its length here: a single flat "cheerful voice" description would lose exactly the quality that makes Silver work, the instant, controlled switch between charm and menace.

---

## Notes for the LLM using this template

- Always produce Section 3 as the final deliverable; Section 2 exists to get you there correctly, not to be shipped. Section 4 may partially merge into Section 3 for Rich-register prompts.
- Choose Concise vs. Rich based on the character's importance and complexity, not habit — a minor walk-on doesn't need 2,000 characters, and a novel-spanning antagonist shouldn't be flattened into 20 words just to hit a word-count instinct that isn't actually a model requirement.
- A genuine dynamic character (calm mask over volatile temper, jovial surface over cold calculation) can and should get a Delivery Arc when using the Rich register — this is directly attested in official examples, not a workaround.
- Accent may be included as a bonus descriptor, especially in the Rich register, but shouldn't be relied on as a precise control the way Pitch or Pace are.
- Do not use any "sounds like [celebrity/existing character]" language under any circumstance — this is explicitly blocked regardless of register or length.
