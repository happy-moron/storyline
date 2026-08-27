from dataclasses import dataclass

from storyline.podcast.script_parser import (
    extract_sections,
    parse_section,
    validate_sections,
)

REQUIRED_SECTIONS = ("INTRO", "DIALOGUE", "BREAKDOWN", "OUTRO", "VOICE PROFILES")

# The fix prompt to use for each section when its format fails validation.
SECTION_FIX_PROMPTS = {
    "INTRO": "podcast_fix_script",
    "DIALOGUE": "podcast_fix_dialogue",
    "BREAKDOWN": "podcast_fix_script",
    "OUTRO": "podcast_fix_script",
    "VOICE PROFILES": "podcast_fix_voice_profiles",
}


@dataclass(frozen=True)
class SectionFix:
    section: str
    raw: str
    error: str
    prompt_name: str


@dataclass(frozen=True)
class FixPlan:
    good: dict[str, str]
    fixes: tuple[SectionFix, ...]


def plan_fixes(script_text: str) -> FixPlan:
    sections = extract_sections(script_text)
    errors = validate_sections(script_text)

    good: dict[str, str] = {}
    fixes: list[SectionFix] = []

    for section in REQUIRED_SECTIONS:
        raw = sections.get(section)
        error = errors.get(section)
        if error is not None:
            fixes.append(SectionFix(
                section=section,
                raw=raw or "",
                error=error,
                prompt_name=SECTION_FIX_PROMPTS[section],
            ))
        else:
            good[section] = raw or ""

    _add_cross_section_fix(good, fixes)
    return FixPlan(good=good, fixes=tuple(fixes))


def _add_cross_section_fix(good: dict[str, str], fixes: list[SectionFix]) -> None:
    if "DIALOGUE" not in good or "VOICE PROFILES" not in good:
        return

    dialogue_ids = {line.speaker_id for line in parse_section("DIALOGUE", good["DIALOGUE"])}
    profile_ids = set(parse_section("VOICE PROFILES", good["VOICE PROFILES"]))

    missing = sorted(dialogue_ids - profile_ids)
    extra = sorted(profile_ids - dialogue_ids)
    if not missing and not extra:
        return

    parts = []
    if missing:
        parts.append(
            "Dialogue speakers without a voice profile: "
            + ", ".join(str(sid) for sid in missing)
        )
    if extra:
        parts.append(
            "Voice profiles not used in dialogue: "
            + ", ".join(str(sid) for sid in extra)
        )

    # Voice profiles are derived from the dialogue speakers, so a mismatch is
    # fixed by regenerating the profiles rather than rewriting the dialogue.
    fixes.append(SectionFix(
        section="VOICE PROFILES",
        raw=good.pop("VOICE PROFILES"),
        error="; ".join(parts),
        prompt_name=SECTION_FIX_PROMPTS["VOICE PROFILES"],
    ))


def stitch_script(section_bodies: dict[str, str]) -> str:
    blocks: list[str] = []
    for section in REQUIRED_SECTIONS:
        body = section_bodies.get(section, "").strip()
        blocks.append(section)
        blocks.append("")
        if body:
            blocks.append(body)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"
