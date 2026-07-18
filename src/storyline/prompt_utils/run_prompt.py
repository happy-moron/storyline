import argparse
import concurrent.futures
import importlib.util
import os
import sys
from datetime import datetime, timezone, timedelta

from zsp_llm_client.prompt_runner import PromptRunner

from storyline.logging import get_logger
from .clean_response import strip_think_tags, strip_markdown_fences

_log = get_logger("prompt_utils")

def load_module(module_name):
    """Dynamically import a module located in the current working directory.
    Mirrors the original helper used for optional pre‑processing modules.
    """
    spec = importlib.util.spec_from_file_location(module_name, f"./{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run_prompt(prompt_template_path, input_file_path, output_file_path, pre_process_module=None, models=None,
               timeout: int = 600, extra_options: dict | None = None):
    """Run a prompt, with a wall‑clock timeout (default 600 s / 10 min)."""
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
            extra_options=extra_options,
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


def run_prompt_with_metrics(
    prompt_template_path, input_file_path, output_file_path,
    pre_process_module=None, models=None, timeout: int = 600,
    prompt_label: str = "",
    extra_options: dict | None = None,
) -> dict:
    wall_start = datetime.now(timezone.utc)
    run_prompt(prompt_template_path, input_file_path, output_file_path,
               pre_process_module=pre_process_module, models=models, timeout=timeout,
               extra_options=extra_options)
    wall_end = datetime.now(timezone.utc)
    wall_ms = int((wall_end - wall_start).total_seconds() * 1000)

    metrics = {"wall_ms": wall_ms}

    try:
        from storyline.benchmark.llamacpp_log_parser import parse_window

        records = parse_window(
            start=wall_start - timedelta(seconds=2),
            end=wall_end + timedelta(seconds=5),
        )
        meaningful = [r for r in records
                      if r.total_tokens is not None and r.total_tokens > 50]
        if meaningful:
            rec = meaningful[-1]
            metrics.update({
                "prompt_tokens": rec.prompt_eval_tokens,
                "eval_tokens": rec.eval_tokens,
                "prompt_tps": rec.prompt_eval_tokens_per_sec,
                "eval_tps": rec.eval_tokens_per_sec,
                "server_total_ms": rec.total_time_ms,
                "cache": rec.cache_selected_by,
                "model_hint": rec.model_hint,
            })
    except Exception:
        pass

    return metrics

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
