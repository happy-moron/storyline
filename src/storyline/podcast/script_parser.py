import re
from dataclasses import dataclass
from typing import ClassVar, Union


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ScriptParseError(Exception):
    def __init__(self, message: str, section: str | None = None):
        self.section = section
        prefix = f"[{section}] " if section else ""
        super().__init__(f"{prefix}{message}")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostLine:
    lang: str  # 'en' or 'zh'
    speaker: str  # 'teacher' or 'student'
    text: str


@dataclass(frozen=True)
class DialogueLine:
    speaker_id: int
    chinese: str
    english: str


# Breakdown can contain both host commentary and quoted dialogue lines.
BreakdownLine = Union[HostLine, DialogueLine]


@dataclass(frozen=True)
class VoiceProfile:
    speaker: int
    lang: str
    gender: str
    age: str
    pitch: str
    pace: str
    volume: str
    clarity: str
    fluency: str
    timbre: str
    emotion: str
    usecase: str
    dialogue: str

    MANDATORY_KEYS: ClassVar[tuple[str, ...]] = (
        "speaker", "lang", "gender", "age", "pitch", "pace",
        "volume", "clarity", "fluency", "timbre", "emotion",
        "usecase", "dialogue",
    )


@dataclass(frozen=True)
class PodcastScript:
    intro: list[HostLine]
    dialogue: list[DialogueLine]
    breakdown: list[BreakdownLine]
    outro: list[HostLine]
    voice_profiles: dict[int, VoiceProfile]


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_SECTION_HEADER_RE = re.compile(r"^[A-Z]+(?:\s[A-Z]+)*$")

_HOST_LINE_RE = re.compile(r"^(en|zh)=(teacher|student)$")

_DIALOGUE_SPEAKER_RE = re.compile(r"^zh=(\d+)$")

_VP_KEY_VALUE_RE = re.compile(r'^\s*(\w+)\s*=\s*(.+?)\s*$')

_REQUIRED_SECTIONS = ("INTRO", "DIALOGUE", "BREAKDOWN", "OUTRO", "VOICE PROFILES")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_script(text: str) -> PodcastScript:
    sections = _extract_sections(text)
    _validate_required_sections(sections)

    intro = _parse_host_section(sections["INTRO"], "INTRO", strict=True)
    dialogue = _parse_dialogue_section(sections["DIALOGUE"])
    breakdown = _parse_breakdown_section(sections["BREAKDOWN"])
    outro = _parse_host_section(sections["OUTRO"], "OUTRO", strict=True)
    voice_profiles = _parse_voice_profiles_section(sections["VOICE PROFILES"])

    dialogue_speaker_ids = {line.speaker_id for line in dialogue}
    _validate_dialogue_voice_profile_consistency(dialogue_speaker_ids, voice_profiles)

    return PodcastScript(
        intro=intro,
        dialogue=dialogue,
        breakdown=breakdown,
        outro=outro,
        voice_profiles=voice_profiles,
    )


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

def _extract_sections(text: str) -> dict[str, str]:
    lines = text.split("\n")
    current_section: str | None = None
    sections: dict[str, list[str]] = {}
    current_lines: list[str] = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped and current_section is None:
            continue

        if _is_section_header(stripped):
            if current_section is not None:
                sections[current_section] = current_lines
            current_section = stripped
            current_lines = []
            continue

        if current_section is not None:
            current_lines.append(stripped)

    if current_section is not None:
        sections[current_section] = current_lines

    return {k: "\n".join(v) for k, v in sections.items()}


def _is_section_header(line: str) -> bool:
    return bool(_SECTION_HEADER_RE.match(line))


def _validate_required_sections(sections: dict[str, str]) -> None:
    for required in _REQUIRED_SECTIONS:
        if required not in sections:
            raise ScriptParseError(f"Missing section: {required}")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _strip_surrounding_blanks(raw: list[str]) -> list[str]:
    result: list[str] = []
    in_content = False
    trailing: list[str] = []
    for l in raw:
        if l.strip():
            in_content = True
            result.extend(trailing)
            trailing.clear()
            result.append(l)
        elif in_content:
            trailing.append(l)
    return result


# ---------------------------------------------------------------------------
# Host section parsing (INTRO, OUTRO — strict: teacher/student only)
# ---------------------------------------------------------------------------

def _parse_host_section(text: str, section_name: str, strict: bool = False) -> list[HostLine]:
    result: list[HostLine] = []
    lines = _strip_surrounding_blanks(text.split("\n"))

    if not lines:
        return result

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        prefix_match = _HOST_LINE_RE.match(line)
        if not prefix_match:
            if strict:
                raise ScriptParseError(
                    f"Invalid host line: expected 'en|zh=teacher|student', got: {line[:40]}",
                    section=section_name,
                )
            i += 1
            continue

        lang = prefix_match.group(1)
        speaker = prefix_match.group(2)

        if i + 1 >= len(lines):
            raise ScriptParseError(
                f"Host line '{line}' has no text after it",
                section=section_name,
            )

        text = lines[i + 1].strip()
        if not text:
            raise ScriptParseError(
                f"Host line for {lang}={speaker} has empty text",
                section=section_name,
            )

        if _HOST_LINE_RE.match(text):
            raise ScriptParseError(
                f"Host line '{line}' has no text after it (got another prefix)",
                section=section_name,
            )

        result.append(HostLine(lang=lang, speaker=speaker, text=text))
        i += 2

    return result


# ---------------------------------------------------------------------------
# Breakdown section: mixed host lines + quoted dialogue lines
# ---------------------------------------------------------------------------

def _parse_breakdown_section(text: str) -> list[BreakdownLine]:
    result: list[BreakdownLine] = []
    lines = _strip_surrounding_blanks(text.split("\n"))

    if not lines:
        return result

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        host_match = _HOST_LINE_RE.match(line)
        if host_match:
            lang = host_match.group(1)
            speaker = host_match.group(2)

            if i + 1 >= len(lines):
                raise ScriptParseError(
                    f"Host line '{line}' has no text after it",
                    section="BREAKDOWN",
                )

            text = lines[i + 1]
            if not text.strip():
                raise ScriptParseError(
                    f"Host line for {lang}={speaker} has empty text",
                    section="BREAKDOWN",
                )

            if _HOST_LINE_RE.match(text) or _DIALOGUE_SPEAKER_RE.match(text):
                raise ScriptParseError(
                    f"Host line '{line}' has no text after it (got another prefix)",
                    section="BREAKDOWN",
                )

            result.append(HostLine(lang=lang, speaker=speaker, text=text.strip()))
            i += 2
            continue

        dialogue_match = _DIALOGUE_SPEAKER_RE.match(line)
        if dialogue_match:
            speaker_id = int(dialogue_match.group(1))

            if i + 1 >= len(lines):
                raise ScriptParseError(
                    f"Missing Chinese text after '{line}'",
                    section="BREAKDOWN",
                )
            chinese = lines[i + 1].strip()
            if not chinese:
                raise ScriptParseError(
                    f"Empty Chinese text after '{line}'",
                    section="BREAKDOWN",
                )

            if i + 2 >= len(lines):
                raise ScriptParseError(
                    f"Missing English translation after Chinese line '{chinese[:30]}'",
                    section="BREAKDOWN",
                )
            english = lines[i + 2].strip()
            if not english:
                raise ScriptParseError(
                    f"Empty English translation after Chinese line '{chinese[:30]}'",
                    section="BREAKDOWN",
                )

            if re.match(r"^(?:zh|en)=", english):
                raise ScriptParseError(
                    f"Missing English translation: got another prefix '{english[:20]}'",
                    section="BREAKDOWN",
                )

            result.append(DialogueLine(speaker_id=speaker_id, chinese=chinese, english=english))
            i += 3
            continue

        raise ScriptParseError(
            f"Invalid line in breakdown: '{line[:40]}'",
            section="BREAKDOWN",
        )

    return result


# ---------------------------------------------------------------------------
# Dialogue section parsing
# ---------------------------------------------------------------------------

def _parse_dialogue_section(text: str) -> list[DialogueLine]:
    result: list[DialogueLine] = []
    lines = _strip_surrounding_blanks(text.split("\n"))

    if not lines:
        raise ScriptParseError("DIALOGUE section is empty", section="DIALOGUE")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        speaker_match = _DIALOGUE_SPEAKER_RE.match(line)
        if not speaker_match:
            if re.match(r"^(en)=\d+$", line):
                raise ScriptParseError(
                    f"Invalid dialogue language: expected 'zh=', got '{line}'",
                    section="DIALOGUE",
                )
            raise ScriptParseError(
                f"Expected 'zh=<speaker_id>', got: '{line[:40]}'",
                section="DIALOGUE",
            )

        speaker_id = int(speaker_match.group(1))

        if i + 1 >= len(lines):
            raise ScriptParseError(
                f"Missing Chinese text after '{line}'",
                section="DIALOGUE",
            )
        chinese = lines[i + 1].strip()
        if not chinese:
            raise ScriptParseError(
                f"Empty Chinese text after '{line}'",
                section="DIALOGUE",
            )

        if i + 2 >= len(lines):
            raise ScriptParseError(
                f"Missing English translation after Chinese line '{chinese[:30]}'",
                section="DIALOGUE",
            )
        english = lines[i + 2].strip()
        if not english:
            raise ScriptParseError(
                f"Empty English translation after Chinese line '{chinese[:30]}'",
                section="DIALOGUE",
            )

        if re.match(r"^(?:zh|en)=", english):
            raise ScriptParseError(
                f"Missing English translation: got another prefix '{english[:20]}'",
                section="DIALOGUE",
            )

        result.append(DialogueLine(speaker_id=speaker_id, chinese=chinese, english=english))
        i += 3

    return result


# ---------------------------------------------------------------------------
# Voice profiles parsing
# ---------------------------------------------------------------------------

def _parse_voice_profiles_section(text: str) -> dict[int, VoiceProfile]:
    lines = _strip_surrounding_blanks(text.split("\n"))

    if not lines:
        raise ScriptParseError("VOICE PROFILES section is empty", section="VOICE PROFILES")

    raw_profiles: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = _VP_KEY_VALUE_RE.match(stripped)
        if not m:
            raise ScriptParseError(
                f"Invalid voice profile line: '{line[:40]}'",
                section="VOICE PROFILES",
            )

        key = m.group(1).strip()
        raw_value = m.group(2).strip()

        value = raw_value.strip('"').strip("'").strip()

        if key in current:
            raw_profiles.append(current)
            current = {}

        current[key] = value

    if current:
        raw_profiles.append(current)

    return _build_voice_profiles(raw_profiles)


def _build_voice_profiles(raw_profiles: list[dict[str, str]]) -> dict[int, VoiceProfile]:
    result: dict[int, VoiceProfile] = {}

    for raw in raw_profiles:
        for key in VoiceProfile.MANDATORY_KEYS:
            if key not in raw:
                raise ScriptParseError(
                    f"Voice profile missing required key: '{key}'",
                    section="VOICE PROFILES",
                )

        for key in raw:
            if key not in VoiceProfile.MANDATORY_KEYS:
                raise ScriptParseError(
                    f"Unknown key in voice profile: '{key}'",
                    section="VOICE PROFILES",
                )

        try:
            speaker_id = int(raw["speaker"])
        except ValueError:
            raise ScriptParseError(
                f"Voice profile speaker must be an integer, got: '{raw['speaker']}'",
                section="VOICE PROFILES",
            )

        if speaker_id in result:
            raise ScriptParseError(
                f"Duplicate voice profile for speaker {speaker_id}",
                section="VOICE PROFILES",
            )

        result[speaker_id] = VoiceProfile(
            speaker=speaker_id,
            lang=raw["lang"],
            gender=raw["gender"],
            age=raw["age"],
            pitch=raw["pitch"],
            pace=raw["pace"],
            volume=raw["volume"],
            clarity=raw["clarity"],
            fluency=raw["fluency"],
            timbre=raw["timbre"],
            emotion=raw["emotion"],
            usecase=raw["usecase"],
            dialogue=raw["dialogue"],
        )

    return result


def _validate_dialogue_voice_profile_consistency(
    dialogue_speaker_ids: set[int],
    voice_profiles: dict[int, VoiceProfile],
) -> None:
    for sid in dialogue_speaker_ids:
        if sid not in voice_profiles:
            raise ScriptParseError(
                f"Dialogue speaker {sid} has no voice profile",
                section="DIALOGUE",
            )

    for sid in voice_profiles:
        if sid not in dialogue_speaker_ids:
            raise ScriptParseError(
                f"Voice profile for speaker {sid} is not used in dialogue",
                section="VOICE PROFILES",
            )