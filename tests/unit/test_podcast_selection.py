import json
from pathlib import Path

import pytest

from storyline.podcast.selection import (
    ExistingEpisodes,
    GrammarPoint,
    append_episode,
    load_existing_episodes,
    load_hsk_grammar_points,
    load_topics,
    next_episode_id,
    next_grammar_points,
    next_topic,
    normalize_name,
    select_next,
)


def _points(*ids):
    return [GrammarPoint(id=i, title=f"title {i}", pattern=f"pattern {i}") for i in ids]


def _existing(grammar=(), themes=(), episodes=()):
    return ExistingEpisodes(
        frozenset(grammar),
        frozenset(themes),
        frozenset(episodes),
    )


class TestNormalizeName:
    def test_simple(self):
        assert normalize_name("Ordering food at a restaurant") == "ordering-food-at-a-restaurant"

    def test_short(self):
        assert normalize_name("Going to the gym") == "going-to-the-gym"

    def test_apostrophe_dropped(self):
        assert (
            normalize_name("Talking about last night's soccer results")
            == "talking-about-last-nights-soccer-results"
        )

    def test_curly_apostrophe_dropped(self):
        assert normalize_name("don’t stop") == "dont-stop"

    def test_punctuation_and_whitespace(self):
        assert normalize_name("  Buying a Car!!  ") == "buying-a-car"

    def test_uppercase(self):
        assert normalize_name("THE Test") == "the-test"

    def test_collapses_separators(self):
        assert normalize_name("a---b   c") == "a-b-c"


class TestNextEpisodeId:
    def test_empty(self):
        assert next_episode_id(_existing()) == 1

    def test_contiguous(self):
        assert next_episode_id(_existing(episodes=(1, 2))) == 3

    def test_single(self):
        assert next_episode_id(_existing(episodes=(5,))) == 6

    def test_non_contiguous(self):
        assert next_episode_id(_existing(episodes=(1, 3))) == 4


class TestNextGrammarPoints:
    def test_no_covered(self):
        points = _points("hsk1-1", "hsk1-2", "hsk1-3")
        result = next_grammar_points(points, frozenset())
        assert [p.id for p in result] == ["hsk1-1", "hsk1-2"]

    def test_first_two_covered(self):
        points = _points("hsk1-1", "hsk1-2", "hsk1-3", "hsk1-4")
        result = next_grammar_points(points, frozenset(["hsk1-1", "hsk1-2"]))
        assert [p.id for p in result] == ["hsk1-3", "hsk1-4"]

    def test_non_contiguous_covered(self):
        points = _points("hsk1-1", "hsk1-2", "hsk1-3", "hsk1-4")
        result = next_grammar_points(points, frozenset(["hsk1-1", "hsk1-3"]))
        assert [p.id for p in result] == ["hsk1-2", "hsk1-4"]

    def test_insufficient_remaining(self):
        points = _points("hsk1-1")
        with pytest.raises(ValueError):
            next_grammar_points(points, frozenset(), n=2)


class TestNextTopic:
    def test_no_covered(self):
        topic, slug = next_topic(["Going to the gym", "Buying a car"], frozenset())
        assert topic == "Going to the gym"
        assert slug == "going-to-the-gym"

    def test_first_covered(self):
        topic, slug = next_topic(
            ["Going to the gym", "Buying a car"], frozenset(["going-to-the-gym"])
        )
        assert topic == "Buying a car"

    def test_non_contiguous_covered(self):
        topics = ["Going to the gym", "Buying a car", "At the beach"]
        topic, slug = next_topic(
            topics, frozenset(["going-to-the-gym", "at-the-beach"])
        )
        assert topic == "Buying a car"
        assert slug == "buying-a-car"

    def test_all_covered(self):
        topics = ["Going to the gym"]
        with pytest.raises(ValueError):
            next_topic(topics, frozenset(["going-to-the-gym"]))


class TestSelectNext:
    def test_full_selection(self):
        points = _points("hsk1-1", "hsk1-2", "hsk1-3", "hsk1-4")
        topics = ["Going to the gym", "Buying a car"]
        existing = _existing(
            grammar=["hsk1-1", "hsk1-3"],
            themes=["going-to-the-gym"],
            episodes=[1],
        )
        selection = select_next(points, topics, existing)
        assert selection.episode_id == 2
        assert [p.id for p in selection.grammar_points] == ["hsk1-2", "hsk1-4"]
        assert selection.theme == "Buying a car"
        assert selection.theme_slug == "buying-a-car"


class TestLoaders:
    def test_load_hsk_grammar_points_order(self, tmp_path):
        def write(name, points):
            data = [{"c": "cat", "g": points}]
            (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")

        write("hsk1-grammar-index.json", [
            ["hsk1-1", "a", "pa"],
            ["hsk1-2", "b", "pb"],
        ])
        write("hsk2-grammar-index.json", [["hsk2-1", "c", "pc"]])
        write("hsk3-grammar-index.json", [["hsk3-1", "d", "pd"]])

        points = load_hsk_grammar_points(tmp_path)
        assert [p.id for p in points] == ["hsk1-1", "hsk1-2", "hsk2-1", "hsk3-1"]

    def test_load_topics_skips_blanks(self, tmp_path):
        path = tmp_path / "topics.md"
        path.write_text("Going to the gym\n\nBuying a car\n   \nAt the beach\n", encoding="utf-8")
        assert load_topics(path) == ["Going to the gym", "Buying a car", "At the beach"]

    def test_load_existing_episodes_missing_file(self, tmp_path):
        result = load_existing_episodes(tmp_path / "nope.csv")
        assert result.grammar_point_ids == frozenset()
        assert result.theme_slugs == frozenset()
        assert result.episode_ids == frozenset()

    def test_load_existing_episodes_empty(self, tmp_path):
        path = tmp_path / "episodes.csv"
        path.write_text("Podcast ID, HSK Point, Theme\n", encoding="utf-8")
        result = load_existing_episodes(path)
        assert result.grammar_point_ids == frozenset()
        assert result.episode_ids == frozenset()

    def test_load_existing_episodes_populated(self, tmp_path):
        path = tmp_path / "episodes.csv"
        path.write_text(
            "Podcast ID, HSK Point, Theme\n"
            "1, hsk1-1, Going to the gym\n"
            "1, hsk1-2, Going to the gym\n"
            "3, hsk1-3, Buying a Car\n",
            encoding="utf-8",
        )
        result = load_existing_episodes(path)
        assert result.grammar_point_ids == frozenset(["hsk1-1", "hsk1-2", "hsk1-3"])
        assert result.theme_slugs == frozenset(["going-to-the-gym", "buying-a-car"])
        assert result.episode_ids == frozenset([1, 3])


class TestAppendEpisode:
    def test_writes_header_and_rows(self, tmp_path):
        path = tmp_path / "episodes.csv"
        selection = select_next(
            _points("hsk1-1", "hsk1-2", "hsk1-3"),
            ["Going to the gym"],
            _existing(),
        )
        append_episode(path, selection)

        text = path.read_text(encoding="utf-8")
        assert "Podcast ID, HSK Point, Theme" in text
        assert text.count("\n") == 3  # header + two rows

    def test_append_does_not_duplicate_header(self, tmp_path):
        path = tmp_path / "episodes.csv"
        path.write_text("Podcast ID, HSK Point, Theme\n", encoding="utf-8")
        selection = select_next(
            _points("hsk1-1", "hsk1-2", "hsk1-3"),
            ["Going to the gym"],
            _existing(),
        )
        append_episode(path, selection)
        append_episode(path, selection)

        header_count = path.read_text(encoding="utf-8").count("Podcast ID")
        assert header_count == 1

    def test_roundtrip_via_loader(self, tmp_path):
        path = tmp_path / "episodes.csv"
        selection = select_next(
            _points("hsk1-1", "hsk1-2", "hsk1-3"),
            ["Going to the gym"],
            _existing(),
        )
        append_episode(path, selection)

        loaded = load_existing_episodes(path)
        assert loaded.episode_ids == frozenset([selection.episode_id])
        assert loaded.grammar_point_ids == frozenset(["hsk1-1", "hsk1-2"])
        assert loaded.theme_slugs == frozenset(["going-to-the-gym"])
