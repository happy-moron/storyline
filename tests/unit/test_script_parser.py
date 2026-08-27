import pytest

from storyline.podcast.script_parser import (
    BreakdownLine,
    DialogueLine,
    HostLine,
    PodcastScript,
    VoiceProfile,
    parse_script,
    parse_section,
    extract_sections,
    validate_sections,
    ScriptParseError,
)


# ---------------------------------------------------------------------------
# Minimal valid fixtures
# ---------------------------------------------------------------------------

MINIMAL_INTRO = """INTRO

en=teacher
Hello, welcome.
en=student
Hi, good to be here.
"""

MINIMAL_DIALOGUE = """DIALOGUE

zh=1
你好吗？
How are you?
zh=2
我很好，谢谢。
I'm fine, thanks.
"""

MINIMAL_BREAKDOWN = """BREAKDOWN

en=teacher
Let's break this down.
en=student
Sounds good.
"""

MINIMAL_OUTRO = """OUTRO

en=teacher
That wraps it up.
en=student
See you next time.
"""

MINIMAL_VOICE_PROFILES = """VOICE PROFILES

speaker = 1
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个年轻人"
dialogue = "你好吗？我很好，谢谢。"

speaker = 2
lang = "zh"
gender = "Female"
age = "二十八岁"
pitch = "中高音域"
pace = "适中"
volume = "中等"
clarity = "非常清晰"
fluency = "流利"
timbre = "柔和"
emotion = "好奇"
usecase = "一个年轻女性"
dialogue = "真的吗？好可惜啊。"
"""


def _full_script(intro=MINIMAL_INTRO, dialogue=MINIMAL_DIALOGUE,
                 breakdown=MINIMAL_BREAKDOWN, outro=MINIMAL_OUTRO,
                 voice_profiles=MINIMAL_VOICE_PROFILES):
    return "\n".join([intro, dialogue, breakdown, outro, voice_profiles])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestParseHappyPath:
    def test_parses_all_sections(self):
        script = parse_script(_full_script())
        assert len(script.intro) == 2
        assert len(script.dialogue) == 2
        assert len(script.breakdown) == 2
        assert len(script.outro) == 2
        assert len(script.voice_profiles) == 2

    def test_intro_lines(self):
        script = parse_script(_full_script())
        assert script.intro[0] == HostLine(lang="en", speaker="teacher", text="Hello, welcome.")
        assert script.intro[1] == HostLine(lang="en", speaker="student", text="Hi, good to be here.")

    def test_dialogue_lines(self):
        script = parse_script(_full_script())
        assert script.dialogue[0] == DialogueLine(speaker_id=1, chinese="你好吗？", english="How are you?")
        assert script.dialogue[1] == DialogueLine(speaker_id=2, chinese="我很好，谢谢。", english="I'm fine, thanks.")

    def test_breakdown_lines(self):
        script = parse_script(_full_script())
        assert script.breakdown[0] == HostLine(lang="en", speaker="teacher", text="Let's break this down.")
        assert script.breakdown[1] == HostLine(lang="en", speaker="student", text="Sounds good.")

    def test_outro_lines(self):
        script = parse_script(_full_script())
        assert script.outro[0] == HostLine(lang="en", speaker="teacher", text="That wraps it up.")
        assert script.outro[1] == HostLine(lang="en", speaker="student", text="See you next time.")

    def test_voice_profiles(self):
        script = parse_script(_full_script())
        assert script.voice_profiles[1] == VoiceProfile(
            speaker=1, lang="zh", gender="Male", age="三十岁",
            pitch="中音域", pace="适中", volume="中等", clarity="清晰",
            fluency="流利", timbre="明亮", emotion="热情",
            usecase="一个年轻人", dialogue="你好吗？我很好，谢谢。",
        )
        assert script.voice_profiles[2] == VoiceProfile(
            speaker=2, lang="zh", gender="Female", age="二十八岁",
            pitch="中高音域", pace="适中", volume="中等", clarity="非常清晰",
            fluency="流利", timbre="柔和", emotion="好奇",
            usecase="一个年轻女性", dialogue="真的吗？好可惜啊。",
        )

    def test_teacher_zh_lines(self):
        intro = """INTRO

zh=teacher
你好，欢迎。
en=student
Hi there.
"""
        script = parse_script(_full_script(intro=intro))
        assert script.intro[0] == HostLine(lang="zh", speaker="teacher", text="你好，欢迎。")
        assert script.intro[1] == HostLine(lang="en", speaker="student", text="Hi there.")

    def test_sections_can_be_empty(self):
        script = parse_script(_full_script(breakdown="BREAKDOWN\n\n"))
        assert script.breakdown == []

    def test_dialogue_many_speakers(self):
        dialogue = """DIALOGUE

zh=1
你好。
Hello.
zh=2
你好吗？
How are you?
zh=3
我很好。
I'm fine.
zh=1
再见。
Goodbye.
"""
        vp = """VOICE PROFILES

speaker = 1
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个年轻人"
dialogue = "你好吗？"

speaker = 2
lang = "zh"
gender = "Female"
age = "二十八岁"
pitch = "中高音域"
pace = "适中"
volume = "中等"
clarity = "非常清晰"
fluency = "流利"
timbre = "柔和"
emotion = "好奇"
usecase = "一个年轻女性"
dialogue = "真的吗？"

speaker = 3
lang = "zh"
gender = "Male"
age = "四十岁"
pitch = "低音域"
pace = "慢"
volume = "响亮"
clarity = "清晰"
fluency = "流利"
timbre = "浑厚"
emotion = "严肃"
usecase = "一个老板"
dialogue = "我很好。"
"""
        script = parse_script(_full_script(dialogue=dialogue, voice_profiles=vp))
        assert len(script.dialogue) == 4
        assert script.dialogue[2].speaker_id == 3


# ---------------------------------------------------------------------------
# Real-world sample
# ---------------------------------------------------------------------------

class TestRealWorldSample:
    @pytest.fixture
    def sample_path(self):
        from pathlib import Path
        return Path(__file__).parent.parent.parent / "books_src" / "podcasts" / "talking-about-last-nights-soccer-results.txt"

    def test_parses_real_world_sample(self, sample_path):
        text = sample_path.read_text(encoding="utf-8")
        script = parse_script(text)

        # Check all sections present
        assert len(script.intro) > 0
        assert len(script.dialogue) > 0
        assert len(script.breakdown) > 0
        assert len(script.outro) > 0
        assert len(script.voice_profiles) > 0

        # All intro/outro lines use teacher/student
        for line in script.intro:
            assert line.speaker in ("teacher", "student")
        for line in script.outro:
            assert line.speaker in ("teacher", "student")

        # Breakdown has both HostLine (teacher/student) and DialogueLine (quoted dialogue)
        for line in script.breakdown:
            if isinstance(line, HostLine):
                assert line.speaker in ("teacher", "student")
            else:
                assert isinstance(line, DialogueLine)
                assert line.speaker_id in script.voice_profiles

        # Dialogue speaker IDs all have voice profiles
        dialogue_speaker_ids = {line.speaker_id for line in script.dialogue}
        for sid in dialogue_speaker_ids:
            assert sid in script.voice_profiles, f"Speaker {sid} missing from voice profiles"

        # Voice profile speaker IDs match dialogue
        for sid in script.voice_profiles:
            assert sid in dialogue_speaker_ids, f"Voice profile {sid} not in dialogue"

    def test_dialogue_lines_coverage(self, sample_path):
        text = sample_path.read_text(encoding="utf-8")
        script = parse_script(text)

        # Every dialogue line has both chinese and english
        for line in script.dialogue:
            assert line.chinese, "Dialogue line has empty chinese"
            assert line.english, "Dialogue line has empty english"

        # English translations don't start with zh= prefix
        for line in script.dialogue:
            assert not line.english.startswith("zh=")

    def test_voice_profile_keys_complete(self, sample_path):
        text = sample_path.read_text(encoding="utf-8")
        script = parse_script(text)

        mandatory = {"speaker", "lang", "gender", "age", "pitch", "pace",
                     "volume", "clarity", "fluency", "timbre", "emotion",
                     "usecase", "dialogue"}

        for sid, profile in script.voice_profiles.items():
            for key in mandatory:
                assert getattr(profile, key) is not None, \
                    f"Profile {sid} missing key: {key}"


# ---------------------------------------------------------------------------
# Section validation
# ---------------------------------------------------------------------------

class TestSectionValidation:
    def test_missing_intro(self):
        script = _full_script(intro="")
        with pytest.raises(ScriptParseError, match="Missing section.*INTRO"):
            parse_script(script)

    def test_missing_dialogue(self):
        script = _full_script(dialogue="")
        with pytest.raises(ScriptParseError, match="Missing section.*DIALOGUE"):
            parse_script(script)

    def test_missing_breakdown(self):
        script = _full_script(breakdown="")
        with pytest.raises(ScriptParseError, match="Missing section.*BREAKDOWN"):
            parse_script(script)

    def test_missing_outro(self):
        script = _full_script(outro="")
        with pytest.raises(ScriptParseError, match="Missing section.*OUTRO"):
            parse_script(script)

    def test_missing_voice_profiles(self):
        script = _full_script(voice_profiles="")
        with pytest.raises(ScriptParseError, match="Missing section.*VOICE PROFILES"):
            parse_script(script)

    def test_missing_multiple_sections(self):
        text = """INTRO

en=teacher
Hello.
""" + """DIALOGUE

zh=1
你好。
Hello.
"""
        with pytest.raises(ScriptParseError):
            parse_script(text)

    def test_unknown_section_header(self):
        script = _full_script() + "\nEXTRA\n\nen=teacher\nHello.\n"
        parse_script(script)  # Should not raise — unknown headers ignored

    def test_typod_section_header(self):
        script = _full_script().replace("BREAKDOWN", "BREAKDONW")
        with pytest.raises(ScriptParseError, match="Missing section.*BREAKDOWN"):
            parse_script(script)


# ---------------------------------------------------------------------------
# Host line format validation (INTRO, BREAKDOWN, OUTRO)
# ---------------------------------------------------------------------------

class TestHostLineValidation:
    def test_invalid_speaker_in_intro(self):
        intro = """INTRO

zh=3
你好。
"""
        script = _full_script(intro=intro)
        with pytest.raises(ScriptParseError, match=r"Invalid host line"):
            parse_script(script)

    def test_invalid_speaker_in_breakdown(self):
        # BREAKDOWN allows zh=N because the teacher quotes dialogue lines.
        # Invalid speaker in breakdown would be a bare text line without prefix.
        breakdown = """BREAKDOWN

This line has no prefix.
"""
        script = _full_script(breakdown=breakdown)
        with pytest.raises(ScriptParseError, match=r"Invalid line in breakdown"):
            parse_script(script)

    def test_invalid_speaker_in_outro(self):
        outro = """OUTRO

zh=3
你好。
"""
        script = _full_script(outro=outro)
        with pytest.raises(ScriptParseError, match=r"Invalid host line"):
            parse_script(script)

    def test_missing_speaker_prefix(self):
        intro = """INTRO

Hello, welcome.
"""
        script = _full_script(intro=intro)
        with pytest.raises(ScriptParseError, match="Invalid host line"):
            parse_script(script)

    def test_invalid_lang_prefix(self):
        intro = """INTRO

fr=teacher
Bonjour.
"""
        script = _full_script(intro=intro)
        with pytest.raises(ScriptParseError, match="Invalid host line"):
            parse_script(script)

    def test_empty_host_line_text(self):
        intro = """INTRO

en=teacher

en=student
Hi.
"""
        script = _full_script(intro=intro)
        with pytest.raises(ScriptParseError, match="empty text"):
            parse_script(script)


# ---------------------------------------------------------------------------
# Dialogue format validation
# ---------------------------------------------------------------------------

class TestDialogueValidation:
    def test_zh_line_without_english_translation(self):
        dialogue = """DIALOGUE

zh=1
你好。
zh=2
你好吗？
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Missing English translation"):
            parse_script(script)

    def test_zh_line_at_eof_no_translation(self):
        dialogue = """DIALOGUE

zh=1
你好。"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Missing English translation"):
            parse_script(script)

    def test_translation_with_zh_prefix(self):
        dialogue = """DIALOGUE

zh=1
你好。
zh=2
Hello.
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Missing English translation"):
            parse_script(script)

    def test_non_numeric_dialogue_speaker(self):
        dialogue = """DIALOGUE

zh=abc
你好。
Hello.
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Expected.*zh="):
            parse_script(script)

    def test_non_zh_lang_in_dialogue(self):
        dialogue = """DIALOGUE

en=1
Hello.
你好。
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Invalid dialogue language"):
            parse_script(script)

    def test_dialogue_line_starts_with_text_not_prefix(self):
        dialogue = """DIALOGUE

Hello, this is wrong.
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Expected.*zh="):
            parse_script(script)

    def test_empty_chinese_text(self):
        dialogue = """DIALOGUE

zh=1

Hello.
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Empty Chinese text"):
            parse_script(script)

    def test_empty_english_text(self):
        dialogue = """DIALOGUE

zh=1
你好。

zh=2
你好吗？
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="Empty English translation"):
            parse_script(script)

    def test_empty_dialogue_section(self):
        dialogue = """DIALOGUE

"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="DIALOGUE section is empty"):
            parse_script(script)


# ---------------------------------------------------------------------------
# Voice profile validation
# ---------------------------------------------------------------------------

class TestVoiceProfileValidation:
    def _profiles(self, profiles_text):
        return _full_script(voice_profiles="VOICE PROFILES\n\n" + profiles_text)

    def test_missing_voice_profiles_section_content(self):
        vp = """VOICE PROFILES

"""
        script = _full_script(voice_profiles=vp)
        with pytest.raises(ScriptParseError, match="VOICE PROFILES section is empty"):
            parse_script(script)

    def test_missing_mandatory_key(self):
        vp = """VOICE PROFILES

speaker = 1
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个年轻人"
"""
        script = _full_script(voice_profiles=vp)
        with pytest.raises(ScriptParseError, match="missing required key.*dialogue"):
            parse_script(script)

    def test_invalid_speaker_value(self):
        vp = """VOICE PROFILES

speaker = abc
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个年轻人"
dialogue = "测试。"
"""
        script = _full_script(voice_profiles=vp)
        with pytest.raises(ScriptParseError, match="speaker.*must be an integer"):
            parse_script(script)

    def test_unknown_key_in_profile(self):
        vp = """VOICE PROFILES

speaker = 1
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个年轻人"
dialogue = "测试。"
favourite_colour = "blue"
"""
        script = _full_script(voice_profiles=vp)
        with pytest.raises(ScriptParseError, match="Unknown key.*favourite_colour"):
            parse_script(script)

    def test_duplicate_speaker_id(self):
        vp = """VOICE PROFILES

speaker = 1
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个年轻人"
dialogue = "你好吗？"

speaker = 2
lang = "zh"
gender = "Female"
age = "二十八岁"
pitch = "中高音域"
pace = "适中"
volume = "中等"
clarity = "非常清晰"
fluency = "流利"
timbre = "柔和"
emotion = "好奇"
usecase = "一个年轻女性"
dialogue = "真的吗？"

speaker = 1
lang = "zh"
gender = "Male"
age = "四十岁"
pitch = "低音域"
pace = "慢"
volume = "响亮"
clarity = "清晰"
fluency = "流利"
timbre = "浑厚"
emotion = "严肃"
usecase = "一个老板"
dialogue = "不行。"
"""
        script = _full_script(voice_profiles=vp)
        with pytest.raises(ScriptParseError, match="Duplicate voice profile"):
            parse_script(script)

    def test_dialogue_speaker_missing_profile(self):
        # Add speaker 3 in dialogue but no profile
        dialogue = """DIALOGUE

zh=1
你好。
Hello.
zh=3
你好！
Hi!
"""
        script = _full_script(dialogue=dialogue)
        with pytest.raises(ScriptParseError, match="no voice profile"):
            parse_script(script)

    def test_voice_profile_without_dialogue_usage(self):
        # Profile for speaker 3 who never appears in dialogue
        vp = """VOICE PROFILES

speaker = 1
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个年轻人"
dialogue = "你好吗？"

speaker = 2
lang = "zh"
gender = "Female"
age = "二十八岁"
pitch = "中高音域"
pace = "适中"
volume = "中等"
clarity = "非常清晰"
fluency = "流利"
timbre = "柔和"
emotion = "好奇"
usecase = "一个年轻女性"
dialogue = "真的吗？"

speaker = 99
lang = "zh"
gender = "Male"
age = "四十岁"
pitch = "低音域"
pace = "慢"
volume = "大"
clarity = "清晰"
fluency = "流利"
timbre = "浑厚"
emotion = "严肃"
usecase = "一个老板"
dialogue = "不行。"
"""
        script = _full_script(voice_profiles=vp)
        with pytest.raises(ScriptParseError, match="Voice profile for speaker 99.*not used"):
            parse_script(script)

    def test_voice_profile_values_unquoted(self):
        vp = """VOICE PROFILES

speaker = 1
lang = zh
gender = Male
age = 三十岁
pitch = 中音域
pace = 适中
volume = 中等
clarity = 清晰
fluency = 流利
timbre = 明亮
emotion = 热情
usecase = 一个年轻人
dialogue = 你好吗？我很好。

speaker = 2
lang = zh
gender = Female
age = 二十八岁
pitch = 中高音域
pace = 适中
volume = 中等
clarity = 非常清晰
fluency = 流利
timbre = 柔和
emotion = 好奇
usecase = 一个年轻女性
dialogue = 真的吗？好可惜啊。
"""
        script = parse_script(_full_script(voice_profiles=vp))
        assert script.voice_profiles[1].lang == "zh"
        assert script.voice_profiles[1].gender == "Male"
        assert script.voice_profiles[1].dialogue == "你好吗？我很好。"
        assert script.voice_profiles[2].lang == "zh"
        assert script.voice_profiles[2].gender == "Female"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_extra_whitespace_between_sections(self):
        script = "\n\n\n".join([
            MINIMAL_INTRO, MINIMAL_DIALOGUE, MINIMAL_BREAKDOWN,
            MINIMAL_OUTRO, MINIMAL_VOICE_PROFILES,
        ])
        result = parse_script(script)
        assert len(result.intro) == 2
        assert len(result.dialogue) == 2

    def test_blank_lines_within_sections(self):
        intro = """INTRO

en=teacher
Line one.

en=student
Line two.
"""
        result = parse_script(_full_script(intro=intro))
        assert len(result.intro) == 2

    def test_voice_profile_quoted_values_with_equals(self):
        vp = """VOICE PROFILES

speaker = 1
lang = "zh"
gender = "Male"
age = "三十岁"
pitch = "中音域"
pace = "适中"
volume = "中等"
clarity = "清晰"
fluency = "流利"
timbre = "明亮"
emotion = "热情"
usecase = "一个 = 年轻人"
dialogue = "测试 = 对话。"

speaker = 2
lang = "zh"
gender = "Female"
age = "二十八岁"
pitch = "中高音域"
pace = "适中"
volume = "中等"
clarity = "非常清晰"
fluency = "流利"
timbre = "柔和"
emotion = "好奇"
usecase = "一个年轻女性"
dialogue = "真的吗？"
"""
        script = parse_script(_full_script(voice_profiles=vp))
        assert script.voice_profiles[1].usecase == "一个 = 年轻人"
        assert script.voice_profiles[1].dialogue == "测试 = 对话。"

    def test_script_with_only_required_sections_empty_optional(self):
        script = _full_script()
        result = parse_script(script)
        assert isinstance(result, PodcastScript)

    def test_breakdown_with_zh_teacher_lines(self):
        script = _full_script()
        result = parse_script(script)
        # Minimal breakdown only has host lines
        for line in result.breakdown:
            assert isinstance(line, HostLine)
            assert line.speaker in ("teacher", "student")
        assert all(line.speaker in ("teacher", "student") for line in result.intro)
        assert all(line.speaker in ("teacher", "student") for line in result.outro)


# ---------------------------------------------------------------------------
# Dataclass immutability
# ---------------------------------------------------------------------------

class TestExtractSections:
    def test_extracts_known_sections_without_headers(self):
        sections = extract_sections(_full_script())
        assert set(sections) == {"INTRO", "DIALOGUE", "BREAKDOWN", "OUTRO", "VOICE PROFILES"}
        assert "INTRO" not in sections["INTRO"]
        assert "en=teacher" in sections["INTRO"]

    def test_missing_section_absent(self):
        text = "INTRO\n\nen=teacher\nHello.\n\nDIALOGUE\n\nzh=1\n你好。\nHello.\n"
        sections = extract_sections(text)
        assert "BREAKDOWN" not in sections
        assert "OUTRO" not in sections
        assert "VOICE PROFILES" not in sections


class TestParseSection:
    def test_parses_each_section_independently(self):
        sections = extract_sections(_full_script())
        assert len(parse_section("INTRO", sections["INTRO"])) == 2
        assert len(parse_section("DIALOGUE", sections["DIALOGUE"])) == 2
        assert len(parse_section("BREAKDOWN", sections["BREAKDOWN"])) == 2
        assert len(parse_section("OUTRO", sections["OUTRO"])) == 2
        assert len(parse_section("VOICE PROFILES", sections["VOICE PROFILES"])) == 2

    def test_unknown_section_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown section"):
            parse_section("NOPE", "en=teacher\nHello.")

    def test_invalid_dialogue_raises_script_parse_error(self):
        with pytest.raises(ScriptParseError):
            parse_section("DIALOGUE", "zh=1\n你好。\n")


class TestValidateSections:
    def test_valid_script_has_no_errors(self):
        assert validate_sections(_full_script()) == {}

    def test_reports_only_bad_sections(self):
        text = _full_script(
            dialogue="DIALOGUE\n\nzh=1\n你好。\n",
            voice_profiles="VOICE PROFILES\n\n",
        )
        errors = validate_sections(text)
        assert "DIALOGUE" in errors
        assert "VOICE PROFILES" in errors
        assert "INTRO" not in errors
        assert "BREAKDOWN" not in errors
        assert "OUTRO" not in errors

    def test_reports_missing_section(self):
        text = "INTRO\n\nen=teacher\nHello.\n\nDIALOGUE\n\nzh=1\n你好。\nHello.\n"
        errors = validate_sections(text)
        assert errors["BREAKDOWN"] == "Missing section: BREAKDOWN"
        assert errors["OUTRO"] == "Missing section: OUTRO"
        assert errors["VOICE PROFILES"] == "Missing section: VOICE PROFILES"

    def test_cross_section_mismatch_not_reported(self):
        # Dialogue references speaker 3 with no profile, but validate_sections
        # only checks individual section formatting.
        dialogue = """DIALOGUE

zh=1
你好。
Hello.
zh=3
再见。
Goodbye.
"""
        text = _full_script(dialogue=dialogue)
        assert validate_sections(text) == {}


class TestDataclassImmutability:
    def test_host_line_is_frozen(self):
        line = HostLine(lang="en", speaker="teacher", text="Hello")
        with pytest.raises(Exception):
            line.text = "changed"

    def test_dialogue_line_is_frozen(self):
        line = DialogueLine(speaker_id=1, chinese="你好", english="Hello")
        with pytest.raises(Exception):
            line.speaker_id = 2

    def test_voice_profile_is_frozen(self):
        vp = VoiceProfile(
            speaker=1, lang="zh", gender="Male", age="三十",
            pitch="中", pace="中", volume="中", clarity="清",
            fluency="流", timbre="亮", emotion="热",
            usecase="人", dialogue="话",
        )
        with pytest.raises(Exception):
            vp.speaker = 2