# Voice Design Template

## Core Vocal Parameters 

A fixed set of short-form fields that the TTS model actually consumes to render audio. These are always present, always terse (phrases, not sentences), and always in the same order, so the pipeline can parse them consistently.


Each field below should be a short phrase (2–8 words), not a full sentence. Omit a field only if it's genuinely not applicable; don't leave placeholders.

| Field | Purpose | Form / Guidance |
|---|---|---|
| **Gender** | Sets base voice bank | `Male` / `Female` / specify if androgynous or character-simulated (e.g. "female voice simulating a male child") |
| **Pitch** | Base register + any movement within it | Register (low/mid/high) + direction of movement if it shifts, e.g. "Mid-range, rising sharply with agitation" |
| **Tempo / Speed** | Rate of speech, and whether it changes over the line | Baseline pace + any acceleration/deceleration trigger, e.g. "Measured, then rapid during outburst" |
| **Volume** | Loudness and its trajectory | Baseline + shifts, e.g. "Conversational, escalating to near-shout at peaks" |
| **Age** | Perceived age of the speaker, not the pitch itself | A decade or life-stage, e.g. "Late 20s" / "Middle-aged to elderly" |
| **Clarity** | How crisply words are formed | e.g. "Crisp, every syllable distinct" vs "Soft, slightly slurred" |
| **Fluency** | Smoothness of delivery — hesitations, stammering, fillers | e.g. "Fluent, no hesitation" vs "Stammers when nervous, uneven rhythm" |
| **Accent** | Language/regional accent | e.g. "Standard Mandarin", "British English", "General American" |
| **Timbre / Texture** | The grain/quality of the voice itself | e.g. "Rich, slightly husky", "Bright and clear", "Gritty, gravelly" |

| **Emotion** | The felt emotional state, and how it moves during the line | Starting emotion → ending emotion if it shifts, e.g. "Starts impatient, turns to angry reproach" |
| **Tone** | The rhetorical stance the line takes (how it lands on a listener) | e.g. "Imperative, brooking no dissent", "Accusatory and confrontational" |
| **Personality** | The character trait the voice should telegraph, distinct from momentary emotion | 2–4 adjectives, e.g. "Authoritative, decisive" or "Theatrical and expressive" |

---

## Example A — Recurring major character 

**Character Name:** Major Jones

**Core Vocal Parameters**
- Gender: Male
- Pitch: Low male register, stable
- Tempo: Slightly fast, tight rhythm
- Volume: Loud, strong projection
- Age: Late 60s
- Clarity: Clear enunciation, forceful phrasing
- Fluency: Smooth, seamless delivery
- Accent: British English
- Timbre: Rich, slightly husky
- Emotion: Solemn, resolute
- Tone: Imperative, emphasizing decisiveness
- Personality: Authoritative, decisive, brooks no dissent

---

## Example B

**Character Name:** Xiao Lin

**Core Vocal Parameters**
- Gender: Male
- Pitch: Mid male register, clear but unstable under stress
- Tempo: Alternates fast and slow; stammers when nervous
- Volume: Low murmur, spiking to sudden agitation
- Age: Mid-20s
- Clarity: Generally clear, breaks down when flustered
- Fluency: Hesitant, audible stammering at emotional peaks
- Accent: Standard Mandarin
- Timbre: Light, youthful
- Emotion: Moves from quiet self-doubt to sudden agitation to a sighing resignation
- Tone: Uncertain, self-questioning, occasionally pleading
- Personality: Anxious, earnest, easily rattled — an ordinary office worker out of his depth

---
