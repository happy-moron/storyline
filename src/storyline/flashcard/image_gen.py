import json
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

from storyline.logging import get_logger
from storyline.services.manager import ServiceManager

_log = get_logger("flashcard.image")

_STYLE_SUFFIX = ", coloring book style, line drawing, simple, black and white"

_WORKFLOW_PATH = Path(__file__).resolve().parent / "flux2_klein_distilled.json"

_PROMPT_NODE_ID = "76"  # PrimitiveStringMultiline — holds the prompt text


def _load_workflow() -> dict:
    with open(_WORKFLOW_PATH, encoding="utf-8") as f:
        return json.load(f)


def _inject_prompt(workflow: dict, positive: str) -> dict:
    workflow[_PROMPT_NODE_ID]["inputs"]["value"] = positive
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


def _invert_image(input_path: Path, output_path: Path) -> Path:
    img = Image.open(str(input_path)).convert("L")
    inverted = ImageOps.invert(img)
    inverted.save(str(output_path))
    return output_path


def generate_images(
    prompts: list[str],
    output_dir: Path,
    service_manager: ServiceManager,
    timeout_per_image: int = 600,
) -> list[Path]:
    if len(prompts) != 6:
        raise ValueError(f"Expected 6 prompts, got {len(prompts)}")

    output_dir.mkdir(parents=True, exist_ok=True)

    service_manager.start_if_needed("image_gen")
    config = service_manager.get_config("image_gen")
    base_url = config.base_url or f"http://127.0.0.1:{config.port}"

    workflow = _load_workflow()

    image_paths: list[Path] = []

    for i, raw_prompt in enumerate(prompts):
        full_prompt = raw_prompt + _STYLE_SUFFIX
        output_path = output_dir / f"img_{i + 1}.png"
        image_paths.append(output_path)

        _log.info(
            "event=image_gen_start index=%d prompt=%s",
            i + 1, full_prompt,
        )

        _inject_prompt(workflow, full_prompt)

        try:
            prompt_id = _queue_prompt(base_url, workflow)
            _log.info("event=image_queued index=%d prompt_id=%s", i + 1, prompt_id)

            history_entry = _wait_for_completion(
                base_url, prompt_id, timeout=timeout_per_image
            )
            image_info = _find_output_image(history_entry)

            _download_image(base_url, image_info, output_path)
            _log.info(
                "event=image_gen_ok index=%d file=%s",
                i + 1, image_info["filename"],
            )

            inverted_path = output_dir / f"img_{i + 1}_inverted.png"
            _invert_image(output_path, inverted_path)
            _log.info(
                "event=image_invert_ok index=%d path=%s",
                i + 1, inverted_path.name,
            )
        except Exception as e:
            _log.error("event=image_gen_fail index=%d error=%s", i + 1, str(e))
            raise

    return image_paths
