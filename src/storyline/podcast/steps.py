"""Podcast sequence planning — builds ordered lists of audio Steps.

Separated from mp3_gen.py to keep the audio-rendering module focused.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Union

from pydub import AudioSegment

from storyline.podcast.script_parser import HostLine, PodcastScript


# ---------------------------------------------------------------------------
# Audio step model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostStep:
    speaker: str
    lang: str
    text: str


@dataclass(frozen=True)
class CharacterStep:
    speaker_id: int
    chinese: str
    dialogue_index: int | None = None


Step = Union[HostStep, CharacterStep]


# ---------------------------------------------------------------------------
# Sequence builders
# ---------------------------------------------------------------------------

def _build_dialogue_with_transitions(
    script: PodcastScript,
    repetitions: int,
    first_label: str,
    repeat_label: str = "Second time",
    final_label: str = "Third time",
) -> list[Step]:
    steps: list[Step] = []

    if repetitions <= 0:
        return steps

    steps.append(HostStep("teacher", "en", first_label))
    for index, line in enumerate(script.dialogue):
        steps.append(CharacterStep(line.speaker_id, line.chinese, index))

    transition_labels = [repeat_label, final_label]
    for rep in range(1, repetitions):
        if rep - 1 < len(transition_labels):
            steps.append(HostStep("teacher", "en", transition_labels[rep - 1]))
        for index, line in enumerate(script.dialogue):
            steps.append(CharacterStep(line.speaker_id, line.chinese, index))

    return steps


def _build_line_by_line_with_transitions(
    script: PodcastScript,
    repetitions: int,
    first_label: str,
    repeat_label: str = "Second time",
    final_label: str = "Third time",
) -> list[Step]:
    steps: list[Step] = []

    if repetitions <= 0:
        return steps

    steps.append(HostStep("teacher", "en", first_label))
    for line in script.dialogue:
        for _ in range(repetitions):
            steps.append(HostStep("teacher", "zh", line.chinese))
            steps.append(HostStep("student", "en", line.english))

    return steps


def dialogue_index_map(script: PodcastScript) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, line in enumerate(script.dialogue):
        if line.chinese not in result:
            result[line.chinese] = index
    return result


def build_flashcard_intro_sequence(
    flashcard_entries: list[list[str]],
    flashcard_audio_dir: Path | None = None,
) -> tuple[list[Step], dict[tuple[str, str, str], AudioSegment]]:
    steps: list[Step] = []
    preloaded: dict[tuple[str, str, str], AudioSegment] = {}
    if not flashcard_entries:
        return steps, preloaded

    steps.append(HostStep("teacher", "en", "The vocab in this lesson is："))

    audio_dir = Path(flashcard_audio_dir) if flashcard_audio_dir else None

    for i, entry in enumerate(flashcard_entries):
        word = entry[0]
        meaning = entry[2]
        sentence = entry[3]
        translation = entry[5]
        idx = i + 1

        steps.append(HostStep("teacher", "en", f"The chinese word... {word}"))
        steps.append(HostStep("teacher", "en", f"This means... {meaning}"))
        steps.append(HostStep("teacher", "zh", f"A sample sentence is... {sentence}"))
        steps.append(HostStep("teacher", "en", f"This means... {translation}"))
        steps.append(HostStep("teacher", "zh", sentence))
        steps.append(HostStep("teacher", "en", translation))
        steps.append(HostStep("teacher", "zh", sentence))
        steps.append(HostStep("teacher", "en", translation))

        if audio_dir:
            sentence_audio_path = audio_dir / f"audio_{idx}_sentence.mp3"
            if sentence_audio_path.is_file():
                audio = AudioSegment.from_file(str(sentence_audio_path))
                cache_key = ("teacher", "zh", sentence)
                preloaded[cache_key] = audio

    return steps, preloaded


def build_podcast_sequence(
    script: PodcastScript,
    dialogue_repetitions: int = 3,
    line_by_line_repetitions: int = 3,
    flashcard_intro: list[Step] | None = None,
) -> list[Step]:
    steps: list[Step] = []

    if flashcard_intro:
        steps.extend(flashcard_intro)

    for line in script.intro:
        steps.append(HostStep(line.speaker, line.lang, line.text))

    steps.extend(
        _build_dialogue_with_transitions(
            script, dialogue_repetitions,
            first_label="Now we'll hear the dialogue three times",
        )
    )

    steps.extend(
        _build_line_by_line_with_transitions(
            script, line_by_line_repetitions,
            first_label="Now we'll hear the dialogue with translation three times",
        )
    )

    index_by_chinese = dialogue_index_map(script)
    for item in script.breakdown:
        if isinstance(item, HostLine):
            steps.append(HostStep(item.speaker, item.lang, item.text))
        else:
            steps.append(
                CharacterStep(
                    item.speaker_id,
                    item.chinese,
                    index_by_chinese.get(item.chinese),
                )
            )

    steps.extend(
        _build_dialogue_with_transitions(
            script, dialogue_repetitions,
            first_label="Let's hear the dialogue three more times",
        )
    )

    for line in script.outro:
        steps.append(HostStep(line.speaker, line.lang, line.text))

    return steps