from storyline.podcast.create_podcast import build_script_input, build_vocab_input
from storyline.podcast.selection import GrammarPoint, Selection


def _selection():
    return Selection(
        episode_id=1,
        grammar_points=[
            GrammarPoint(id="hsk1-1", title='Expressing "zai"', pattern="(正) 在 + Verb"),
            GrammarPoint(id="hsk1-2", title='Negation with "mei"', pattern="没 + 有"),
        ],
        theme="Ordering food at a restaurant",
        theme_slug="ordering-food-at-a-restaurant",
    )


class TestBuildVocabInput:
    def test_appends_theme(self):
        assert build_vocab_input("Going to the gym") == "Going to the gym\n"


class TestBuildScriptInput:
    def test_includes_sections(self):
        text = build_script_input(_selection(), "比赛- bǐsài - match/game\n")
        assert "## HSK Point(s)" in text
        assert "## Theme" in text
        assert "## Vocab" in text

    def test_formats_grammar_points(self):
        text = build_script_input(_selection(), "词- cí - word\n")
        assert '["hsk1-1", "Expressing \\"zai\\"", "(正) 在 + Verb"]' in text
        assert '["hsk1-2", "Negation with \\"mei\\"", "没 + 有"]' in text

    def test_includes_theme_and_vocab(self):
        text = build_script_input(_selection(), " 词- cí - word\n ")
        assert "Ordering food at a restaurant" in text
        assert "词- cí - word" in text
