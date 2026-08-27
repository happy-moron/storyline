import pytest

from storyline.config.pipeline_config import PipelineConfig
from storyline.podcast.run_pipeline import run_full_pipeline


# ---------------------------------------------------------------------------
# The full pipeline requires LLM + TTS services, so these tests focus on
# import-time correctness and argument plumbing.  Full integration is tested
# by running the module directly against live services.
# ---------------------------------------------------------------------------

class TestRunFullPipelineModule:
    """Verify that the module interface is importable and well-formed."""

    def test_function_exists(self):
        assert callable(run_full_pipeline)

    def test_config_accepts_skip_audio(self):
        config = PipelineConfig()
        config.llm_provider = "remote"
        # Should not blow up on arg validation
        assert config.skip_audio is False


class TestPipelineConfigDefaults:
    def test_skip_audio_defaults_false(self):
        cfg = PipelineConfig()
        assert cfg.skip_audio is False

    def test_cli_skip_audio_applied(self):
        class Args:
            skip_audio = True
            models = None
            max_chunks = None
            skip_simplify = None
            audio_profile = None
            audio_use_instruct = None
        cfg = PipelineConfig.from_files_and_args(Args())
        assert cfg.skip_audio is True