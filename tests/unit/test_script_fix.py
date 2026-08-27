from storyline.podcast.script_fix import (
    REQUIRED_SECTIONS,
    SECTION_FIX_PROMPTS,
    plan_fixes,
    stitch_script,
)
from storyline.podcast.script_parser import parse_script


INTRO = """INTRO

en=teacher
Hello, welcome.
en=student
Hi, good to be here.
"""

DIALOGUE = """DIALOGUE

zh=1
你好吗？
How are you?
zh=2
我很好，谢谢。
I'm fine, thanks.
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


def _script(intro=INTRO, dialogue=DIALOGUE, breakdown=BREAKDOWN,
            outro=OUTRO, voice_profiles=VOICE_PROFILES):
    return "\n".join([intro, dialogue, breakdown, outro, voice_profiles])


class TestPlanFixes:
    def test_valid_script_has_no_fixes(self):
        plan = plan_fixes(_script())
        assert plan.fixes == ()
        assert set(plan.good) == set(REQUIRED_SECTIONS)

    def test_keeps_good_sections(self):
        bad_dialogue = "DIALOGUE\n\nzh=1\n你好。\n"
        plan = plan_fixes(_script(dialogue=bad_dialogue))

        assert "INTRO" in plan.good
        assert "BREAKDOWN" in plan.good
        assert "OUTRO" in plan.good
        assert "VOICE PROFILES" in plan.good
        assert [f.section for f in plan.fixes] == ["DIALOGUE"]

    def test_bad_dialogue_maps_to_dialogue_prompt(self):
        plan = plan_fixes(_script(dialogue="DIALOGUE\n\nzh=1\n你好。\n"))
        fix = plan.fixes[0]
        assert fix.section == "DIALOGUE"
        assert fix.prompt_name == "podcast_fix_dialogue"
        assert "zh=1" in fix.raw

    def test_bad_voice_profiles_map_to_voice_profiles_prompt(self):
        plan = plan_fixes(_script(voice_profiles="VOICE PROFILES\n\nspeaker = 1\n"))
        fix = plan.fixes[0]
        assert fix.section == "VOICE PROFILES"
        assert fix.prompt_name == "podcast_fix_voice_profiles"

    def test_bad_host_sections_map_to_script_prompt(self):
        plan = plan_fixes(_script(intro="INTRO\n\nzh=3\n你好。\n"))
        assert {f.section for f in plan.fixes} == {"INTRO"}
        assert all(f.prompt_name == "podcast_fix_script" for f in plan.fixes)

    def test_missing_section_has_empty_raw(self):
        text = "INTRO\n\nen=teacher\nHello.\n"
        plan = plan_fixes(text)
        missing = {f.section: f for f in plan.fixes}
        assert missing["DIALOGUE"].raw == ""
        assert missing["DIALOGUE"].error == "Missing section: DIALOGUE"

    def test_cross_section_mismatch_fixes_voice_profiles(self):
        # Dialogue uses speaker 3 with no matching profile.
        dialogue = """DIALOGUE

zh=1
你好。
Hello.
zh=3
再见。
Goodbye.
"""
        plan = plan_fixes(_script(dialogue=dialogue))
        assert "DIALOGUE" in plan.good
        assert "VOICE PROFILES" not in plan.good
        fix = [f for f in plan.fixes if f.section == "VOICE PROFILES"]
        assert len(fix) == 1
        assert "3" in fix[0].error


class TestStitchScript:
    def test_round_trips_valid_script(self):
        plan = plan_fixes(_script())
        stitched = stitch_script(plan.good)
        assert parse_script(stitched).dialogue

    def test_preserves_section_order_and_headers(self):
        stitched = stitch_script({
            "INTRO": INTRO,
            "DIALOGUE": DIALOGUE,
            "BREAKDOWN": BREAKDOWN,
            "OUTRO": OUTRO,
            "VOICE PROFILES": VOICE_PROFILES,
        })
        for section in REQUIRED_SECTIONS:
            assert f"{section}\n" in stitched

    def test_strips_and_normalizes_bodies(self):
        stitched = stitch_script({"INTRO": "\n\nen=teacher\nHello.\n\n"})
        assert stitched.startswith("INTRO\n\nen=teacher\nHello.\n")


class TestPromptMapping:
    def test_all_sections_have_a_prompt(self):
        assert set(SECTION_FIX_PROMPTS) == set(REQUIRED_SECTIONS)

    def test_dialogue_uses_dedicated_prompt(self):
        assert SECTION_FIX_PROMPTS["DIALOGUE"] == "podcast_fix_dialogue"

    def test_voice_profiles_uses_dedicated_prompt(self):
        assert SECTION_FIX_PROMPTS["VOICE PROFILES"] == "podcast_fix_voice_profiles"
