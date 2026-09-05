#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path

import requests

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_PATH = (
    _PROJECT_ROOT / "external_docs" / "comfyui_image-gen" / "hidream_fast_api_example.json"
)

# Node IDs in the hidream workflow
_POSITIVE_PROMPT_NODE = "16"
_SEED_NODE = "3"


def _load_workflow() -> dict:
    with open(_WORKFLOW_PATH, encoding="utf-8") as f:
        return json.load(f)


def _inject_prompt(workflow: dict, positive: str, negative: str | None = None) -> dict:
    workflow[_POSITIVE_PROMPT_NODE]["inputs"]["text"] = positive
    if negative is not None:
        workflow["40"]["inputs"]["text"] = negative
    return workflow


def _queue_prompt(base_url: str, workflow: dict) -> str:
    resp = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    prompt_id = body.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"No prompt_id in response: {body}")
    return prompt_id


def _wait_for_completion(base_url: str, prompt_id: str, timeout: int = 300) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{base_url}/history/{prompt_id}", timeout=10)
        resp.raise_for_status()
        history = resp.json()
        entry = history.get(prompt_id)
        if entry and entry.get("outputs"):
            return entry
        time.sleep(2)
    raise TimeoutError(
        f"Prompt {prompt_id} did not complete within {timeout}s"
    )


def _find_output_image(history_entry: dict) -> dict:
    for node_output in history_entry.get("outputs", {}).values():
        images = node_output.get("images", [])
        if images:
            return images[0]
    raise RuntimeError("No output image found in history entry")


def _download_image(base_url: str, image_info: dict, output_path: Path) -> Path:
    params = {
        "filename": image_info["filename"],
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output"),
    }
    resp = requests.get(f"{base_url}/view", params=params, timeout=60)
    resp.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(resp.content)
    return output_path


def generate_hidream_image(
    prompt_text: str,
    output_path: Path,
    negative_prompt: str = "bad ugly jpeg artifacts",
    timeout: int = 1500,
) -> Path:
    from storyline.services.manager import ServiceManager

    sm = ServiceManager()
    sm.start_if_needed("image_gen")

    config = sm.get_config("image_gen")
    base_url = config.base_url or f"http://127.0.0.1:{config.port}"

    workflow = _load_workflow()
    _inject_prompt(workflow, prompt_text, negative_prompt)

    prompt_id = _queue_prompt(base_url, workflow)
    print(f"Queued prompt: {prompt_id}")

    history_entry = _wait_for_completion(base_url, prompt_id, timeout=timeout)
    image_info = _find_output_image(history_entry)
    print(f"Image generated: {image_info['filename']}")

    return _download_image(base_url, image_info, output_path)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a HiDream image via ComfyUI using a prompt from a file."
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        required=True,
        help="Path to a text file containing the positive prompt.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("hidream_output.png"),
        help="Path for the output image (default: hidream_output.png).",
    )
    parser.add_argument(
        "--negative-prompt",
        type=str,
        default="bad ugly jpeg artifacts",
        help="Negative prompt text (default: 'bad ugly jpeg artifacts').",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1500,
        help="Max seconds to wait for image generation (default: 1500).",
    )
    args = parser.parse_args()

    if not args.prompt_file.is_file():
        print(f"Error: prompt file not found: {args.prompt_file}", file=sys.stderr)
        sys.exit(1)

    prompt_text = args.prompt_file.read_text(encoding="utf-8").strip()
    if not prompt_text:
        print(f"Error: prompt file is empty: {args.prompt_file}", file=sys.stderr)
        sys.exit(1)

    print(prompt_text)

    try:
        result = generate_hidream_image(
            prompt_text=prompt_text,
            output_path=args.output,
            negative_prompt=args.negative_prompt,
            timeout=args.timeout,
        )
        print(f"Saved: {result}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()