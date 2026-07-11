#!/usr/bin/env python3
"""Manual test program for LLM service.

This script performs real integration testing with the LLM service (llamacpp).
It uses the zsp_llm_client library to interact with the OpenAI-compatible API.
"""

import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from zsp_llm_client.prompt_runner import PromptRunner
from storyline.services.manager import ServiceManager, ServiceStatus


def test_llm_service():
    """Test LLM service with real API calls."""
    print("=" * 60)
    print("LLM Service Manual Test")
    print("=" * 60)

    manager = ServiceManager()

    # Check if LLM service is running
    status = manager.get_status('llm')
    print(f"\nLLM Service Status: {status.value}")

    if status == ServiceStatus.OFFLINE:
        print("\nStarting LLM service...")
        if not manager.start('llm'):
            print("ERROR: Failed to start LLM service")
            return False
        print("LLM service started successfully")
        time.sleep(2)  # Wait for service to be ready

    # Test 1: Health check
    print("\n" + "-" * 60)
    print("Test 1: Health Check")
    print("-" * 60)
    try:
        import requests
        response = requests.get('http://127.0.0.1:11432/v1/models', timeout=5)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"ERROR: {e}")
        return False

    # Test 2: Basic text generation
    print("\n" + "-" * 60)
    print("Test 2: Basic Text Generation")
    print("-" * 60)
    try:
        runner = PromptRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_template = Path(tmpdir) / "prompt.txt"
            prompt_template.write_text("Translate to English: {input_content}")

            input_file = Path(tmpdir) / "input.txt"
            input_file.write_text("你好，世界")

            response = runner.run_from_file(str(prompt_template), str(input_file), models=["qwen3-4b"])

            print(f"Input: 你好，世界")
            print(f"Response: {response}")
            print("✓ Text generation successful")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 3: Echo test
    print("\n" + "-" * 60)
    print("Test 3: Echo Test")
    print("-" * 60)
    try:
        runner = PromptRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_template = Path(tmpdir) / "prompt.txt"
            prompt_template.write_text("Echo: {input_content}")

            input_file = Path(tmpdir) / "input.txt"
            input_file.write_text("test input")

            response = runner.run_from_file(str(prompt_template), str(input_file), models=["qwen3-4b"])

            print(f"Input: test input")
            print(f"Response: {response}")
            print("✓ Echo test successful")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 4: Direct text input
    print("\n" + "-" * 60)
    print("Test 4: Direct Text Input")
    print("-" * 60)
    try:
        runner = PromptRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_template = Path(tmpdir) / "prompt.txt"
            prompt_template.write_text("Translate to English: {input_content}")

            response = runner.run(str(prompt_template), "你好，世界", models=["qwen3-4b"])

            print(f"Input: 你好，世界")
            print(f"Response: {response}")
            print("✓ Direct text input successful")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    print("\n" + "=" * 60)
    print("All LLM tests passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    import tempfile

    success = test_llm_service()
    sys.exit(0 if success else 1)
