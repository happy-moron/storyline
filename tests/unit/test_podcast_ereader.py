from storyline.podcast.create_ereader import _slug_from_filename, _title_from_slug


class TestSlugFromFilename:
    def test_basic(self):
        assert _slug_from_filename("talking-about-last-nights-soccer-results.txt") == \
            "talking-about-last-nights-soccer-results"

    def test_full_path(self):
        assert _slug_from_filename(
            "books_src/podcasts/at-the-gym.txt"
        ) == "at-the-gym"

    def test_already_lowercase_with_underscores(self):
        assert _slug_from_filename("my_episode.txt") == "my-episode"

    def test_mixed_case(self):
        assert _slug_from_filename("Talking-About-Food.txt") == "talking-about-food"

    def test_no_extension(self):
        assert _slug_from_filename("simple") == "simple"


class TestTitleFromSlug:
    def test_basic(self):
        assert _title_from_slug("talking-about-last-nights-soccer-results") == \
            "Talking About Last Nights Soccer Results"

    def test_with_underscores(self):
        assert _title_from_slug("at_the_gym") == "At The Gym"

    def test_mixed(self):
        assert _title_from_slug("my-episode-1") == "My Episode 1"