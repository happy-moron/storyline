import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GrammarPoint:
    id: str
    title: str
    pattern: str


@dataclass(frozen=True)
class ExistingEpisodes:
    grammar_point_ids: frozenset[str]
    theme_slugs: frozenset[str]
    episode_ids: frozenset[int]


@dataclass(frozen=True)
class Selection:
    episode_id: int
    grammar_points: list[GrammarPoint]
    theme: str
    theme_slug: str


EPISODES_HEADER = "Podcast ID, HSK Point, Theme"

_APOSTROPHES = str.maketrans("", "", "'’")
_NON_ALPHA = re.compile(r"[^a-z]+")

_HSK_INDEX_FILES = (
    "hsk1-grammar-index.json",
    "hsk2-grammar-index.json",
    "hsk3-grammar-index.json",
)


def normalize_name(text: str) -> str:
    lowered = text.lower().translate(_APOSTROPHES)
    return _NON_ALPHA.sub("-", lowered).strip("-")


def load_hsk_grammar_points(index_dir: Path) -> list[GrammarPoint]:
    points: list[GrammarPoint] = []
    for filename in _HSK_INDEX_FILES:
        with (index_dir / filename).open(encoding="utf-8") as f:
            categories = json.load(f)
        for category in categories:
            for raw in category["g"]:
                points.append(GrammarPoint(id=raw[0], title=raw[1], pattern=raw[2]))
    return points


def load_topics(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_existing_episodes(path: Path) -> ExistingEpisodes:
    if not path.exists():
        return ExistingEpisodes(frozenset(), frozenset(), frozenset())

    grammar_ids: set[str] = set()
    theme_slugs: set[str] = set()
    episode_ids: set[int] = set()

    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, skipinitialspace=True):
            pid = (row.get("Podcast ID") or "").strip()
            grammar = (row.get("HSK Point") or "").strip()
            theme = (row.get("Theme") or "").strip()

            if pid.isdigit():
                episode_ids.add(int(pid))
            if grammar:
                grammar_ids.add(grammar)
            if theme:
                theme_slugs.add(normalize_name(theme))

    return ExistingEpisodes(
        frozenset(grammar_ids),
        frozenset(theme_slugs),
        frozenset(episode_ids),
    )


def next_episode_id(existing: ExistingEpisodes) -> int:
    if not existing.episode_ids:
        return 1
    return max(existing.episode_ids) + 1


def next_grammar_points(
    points: list[GrammarPoint],
    covered: frozenset[str] | set[str],
    n: int = 2,
) -> list[GrammarPoint]:
    result: list[GrammarPoint] = []
    for point in points:
        if point.id in covered:
            continue
        result.append(point)
        if len(result) == n:
            return result
    raise ValueError(f"only {len(result)} unused grammar points remain, need {n}")


def next_topic(
    topics: list[str],
    covered_slugs: frozenset[str] | set[str],
) -> tuple[str, str]:
    for topic in topics:
        slug = normalize_name(topic)
        if slug not in covered_slugs:
            return topic, slug
    raise ValueError("no unused topics remain")


def select_next(
    points: list[GrammarPoint],
    topics: list[str],
    existing: ExistingEpisodes,
    n_grammar: int = 2,
) -> Selection:
    grammar = next_grammar_points(points, existing.grammar_point_ids, n_grammar)
    theme, slug = next_topic(topics, existing.theme_slugs)
    return Selection(
        episode_id=next_episode_id(existing),
        grammar_points=grammar,
        theme=theme,
        theme_slug=slug,
    )


def append_episode(path: Path, selection: Selection) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        if write_header:
            f.write(EPISODES_HEADER + "\n")
        writer = csv.writer(f)
        for point in selection.grammar_points:
            writer.writerow([selection.episode_id, point.id, selection.theme_slug])
