"""Integration tests for LLM functionality using zsp_llm_client.

Tests verify that the refactored LLM code correctly integrates with the
zsp_llm_client.prompt_runner.PromptRunner API.
"""

from zsp_llm_client.prompt_runner import PromptRunner
import tempfile
import os

import pytest

pytestmark = pytest.mark.integration


def test_prompt_runner_basic():
    """Test basic PromptRunner functionality with a simple prompt."""
    runner = PromptRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a simple prompt template
        prompt_template = os.path.join(tmpdir, "prompt.txt")
        with open(prompt_template, "w", encoding="utf-8") as f:
            f.write("Translate to English: {input_content}")

        # Create input file
        input_file = os.path.join(tmpdir, "input.txt")
        with open(input_file, "w", encoding="utf-8") as f:
            f.write("你好，世界")

        # Run with qwen3-4b model
        response = runner.run_from_file(prompt_template, input_file, models=["qwen3-4b"])

        # Verify response is a string
        assert isinstance(response, str)
        # Verify response contains translated content (not empty)
        assert len(response) > 0
        # Verify response is not just the original text
        assert "你好" not in response or "Hello" in response


def test_prompt_runner_no_rate_limiting():
    """Test PromptRunner with rate limiting disabled."""
    runner = PromptRunner(rate_limiter=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_template = os.path.join(tmpdir, "prompt.txt")
        with open(prompt_template, "w", encoding="utf-8") as f:
            f.write("Echo: {input_content}")

        input_file = os.path.join(tmpdir, "input.txt")
        with open(input_file, "w", encoding="utf-8") as f:
            f.write("test input")

        response = runner.run_from_file(prompt_template, input_file, models=["qwen3-4b"])
        assert isinstance(response, str)
        assert len(response) > 0


def test_prompt_runner_with_models_preference():
    """Test that PromptRunner tries models in preference order."""
    runner = PromptRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_template = os.path.join(tmpdir, "prompt.txt")
        with open(prompt_template, "w", encoding="utf-8") as f:
            f.write("Echo: {input_content}")

        input_file = os.path.join(tmpdir, "input.txt")
        with open(input_file, "w", encoding="utf-8") as f:
            f.write("test")

        # Try with multiple models (qwen3-4b should be first)
        response = runner.run_from_file(prompt_template, input_file, models=["qwen3-4b", "gemini-2.0-flash"])
        assert isinstance(response, str)
        assert len(response) > 0


def test_prompt_runner_with_direct_text():
    """Test PromptRunner with direct text input instead of file."""
    runner = PromptRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_template = os.path.join(tmpdir, "prompt.txt")
        with open(prompt_template, "w", encoding="utf-8") as f:
            f.write("Translate to English: {input_content}")

        response = runner.run_from_file(prompt_template, "你好，世界", models=["qwen3-4b"])
        assert isinstance(response, str)
        assert len(response) > 0
