import argparse
import concurrent.futures
import importlib.util
import logging
import os
import sys

from zsp_llm_client.prompt_runner import PromptRunner

from .clean_response import strip_think_tags, strip_markdown_fences

_log = logging.getLogger(__name__)

def load_module(module_name):
    """Dynamically import a module located in the current working directory.
    Mirrors the original helper used for optional pre‑processing modules.
    """
    spec = importlib.util.spec_from_file_location(module_name, f"./{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run_prompt(prompt_template_path, input_file_path, output_file_path, pre_process_module=None, models=None,
               timeout: int = 600):
    """Run a prompt, with a wall‑clock timeout (default 300 s / 5 min).

    The underlying ``llm`` library creates an OpenAI HTTP client whose read
    timeout defaults to 600 s and is not configurable via environment
    variables in openai v2.x.  This wrapper enforces a hard deadline.
    """
    with open(input_file_path, "r", encoding="utf-8", errors="replace") as f:
        raw_input = f.read()
    _log.info("Sending prompt template=%s input=%s (%d chars)",
              prompt_template_path, input_file_path, len(raw_input))

    runner = PromptRunner()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            runner.run_from_file,
            prompt_template_path,
            input_file_path,
            pre_process_module=pre_process_module,
            models=models,
        )
        try:
            response = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(
                f"LLM call for {prompt_template_path!r} timed out after {timeout}s. "
                f"The LLM service may be running but the model may still be loading, "
                f"or the model may not be responding."
            )

    if response is None:
        raise RuntimeError(
            f"PromptRunner returned None for prompt {prompt_template_path!r} "
            f"with input {input_file_path!r}. Check that the LLM service is "
            f"running and the models {models!r} are available."
        )

    response = strip_think_tags(response)
    response = strip_markdown_fences(response)

    _log.info("Response received (%d chars)", len(response))

    with open(output_file_path, "w", encoding="utf-8") as outfile:
        outfile.write(response)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run an LLM prompt with optional pre-processing using zsp-llm-client.")
    parser.add_argument("--prompt_template_path", type=str, required=True)
    parser.add_argument("--input_file_path", type=str, required=True)
    parser.add_argument("--output_file_path", type=str, required=True)
    parser.add_argument("--pre_process_module", type=str, default=None)
    parser.add_argument(
        "--models",
        type=str,
        default="gemini-2.0-flash",
        help="Comma-separated list of models in preference order",
    )

    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(',') if m.strip()]

    run_prompt(
        args.prompt_template_path,
        args.input_file_path,
        args.output_file_path,
        args.pre_process_module,
        models,
    )
