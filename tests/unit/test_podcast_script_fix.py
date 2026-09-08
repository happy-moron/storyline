import pytest

from storyline.config.pipeline_config import PipelineConfig
from storyline.podcast.run_pipeline import (
    _fix_script_by_sections,
    _run_section_fix,
    _strip_section_header,
)
from storyline.podcast.script_parser import parse_script, ScriptParseError


INTRO = """INTRO

en=teacher
Hello, welcome.
en=student
Hi, good to be here.
"""

BAD_DIALOGUE = """DIALOGUE

zh=1
你好。
zh=2
你好吗？
"""

FIXED_DIALOGUE = """zh=1
你好。
Hello.
zh=2
你好吗？
How are you?
"""

BREAKDOWN = """BREAKDOWN

en=teacher
Let's break this down.
en=student
Sounds good.
"""

OUTRO = """OUTRO

en=teacher
That wraps it up.
en=student
See you next time.
"""

VOICE_PROFILES = """VOICE PROFILES

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
dialogue = "你好。"

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
dialogue = "你好吗？"
"""


def _script(dialogue=BAD_DIALOGUE):
    return "\n".join([INTRO, dialogue, BREAKDOWN, OUTRO, VOICE_PROFILES])


class _NullLog:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


class TestStripSectionHeader:
    def test_no_header_unchanged(self):
        assert _strip_section_header("zh=1\n你好。\n", "DIALOGUE") == "zh=1\n你好。\n"

    def test_strips_matching_header(self):
        assert _strip_section_header("DIALOGUE\n\nzh=1\n你好。\n", "DIALOGUE") == "zh=1\n你好。\n"

    def test_keeps_other_allcaps_line(self):
        text = "zh=1\n你好。\n"
        assert _strip_section_header(text, "DIALOGUE") == text


class TestRunSectionFix:
    def test_builds_input_and_returns_body(self, monkeypatch, tmp_path):
        from storyline.podcast import run_pipeline

        class Fix:
            section = "DIALOGUE"
            raw = BAD_DIALOGUE
            error = "Missing English translation"
            prompt_name = "podcast_fix_dialogue"

        captured = {}

        def fake(prompt_template_path, input_file_path, output_file_path, **kwargs):
            captured["input"] = open(input_file_path, encoding="utf-8").read()
            with open(output_file_path, "w", encoding="utf-8") as f:
                f.write(FIXED_DIALOGUE)
            return {"wall_ms": 1}

        monkeypatch.setattr(run_pipeline, "run_prompt_with_metrics", fake)

        config = PipelineConfig()
        body = _run_section_fix(config, Fix(), "", tmp_path, _NullLog(), "test-theme")
        assert body.strip() == FIXED_DIALOGUE.strip()
        assert "Missing English translation" in captured["input"]
        assert "zh=1" in captured["input"]

    def test_garbage_raises(self, monkeypatch, tmp_path):
        from storyline.podcast import run_pipeline

        class Fix:
            section = "DIALOGUE"
            raw = BAD_DIALOGUE
            error = "Missing English translation"
            prompt_name = "podcast_fix_dialogue"

        def fake(prompt_template_path, input_file_path, output_file_path, **kwargs):
            with open(output_file_path, "w", encoding="utf-8") as f:
                f.write("GARBAGE")
            return {"wall_ms": 1}

        monkeypatch.setattr(run_pipeline, "run_prompt_with_metrics", fake)

        config = PipelineConfig()
        with pytest.raises(RuntimeError, match="GARBAGE"):
            _run_section_fix(config, Fix(), "", tmp_path, _NullLog(), "test-theme")


class TestFixScriptBySections:
    def test_reuses_good_sections_and_fixes_bad(self, monkeypatch, tmp_path):
        from storyline.podcast import run_pipeline

        def fake(prompt_template_path, input_file_path, output_file_path, **kwargs):
            with open(output_file_path, "w", encoding="utf-8") as f:
                f.write(FIXED_DIALOGUE)
            return {"wall_ms": 1}

        monkeypatch.setattr(run_pipeline, "run_prompt_with_metrics", fake)

        config = PipelineConfig()
        result = _fix_script_by_sections(config, _script(), tmp_path, _NullLog(), "test-theme")

        parsed = parse_script(result)
        assert len(parsed.dialogue) == 2
        # Good sections preserved verbatim.
        assert "Hello, welcome." in result
        assert "Let's break this down." in result
        # Bad dialogue replaced.
        assert "How are you?" in result

    def test_no_llm_calls_for_valid_script(self, monkeypatch, tmp_path):
        from storyline.podcast import run_pipeline

        # No fixes needed — runner should never be invoked.
        def fake(*args, **kwargs):
            raise AssertionError("should not call LLM")

        monkeypatch.setattr(run_pipeline, "run_prompt_with_metrics", fake)

        good_script = "\n".join([
            INTRO, "DIALOGUE\n\n" + FIXED_DIALOGUE,
            BREAKDOWN, OUTRO, VOICE_PROFILES,
        ])

        config = PipelineConfig()
        result = _fix_script_by_sections(config, good_script, tmp_path, _NullLog(), "test-theme")
        assert parse_script(result).dialogue
