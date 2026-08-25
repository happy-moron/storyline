"""Run a prompt from a single file: start the LLM service, send the file as the prompt, print the response."""

import argparse
import llm

from storyline.services.manager import ServiceManager


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a file as a prompt to the LLM. Starts llamacpp if needed."
    )
    parser.add_argument("file", help="File whose contents will be sent as the prompt")
    parser.add_argument("--profile", default=None,
                        help="LLM profile name (e.g. qwen3-30b-a3b). Switches the model "
                             "loaded by llama.cpp before sending the prompt.")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8", errors="replace") as f:
        prompt_text = f.read()

    mgr = ServiceManager()

    if args.profile:
        mgr.ensure_llm_profile(args.profile)
    else:
        mgr.start_if_needed("llm")

    model = llm.get_model("local-llamacpp")
    response = model.prompt(prompt_text)
    print(response.text())


if __name__ == "__main__":
    main()