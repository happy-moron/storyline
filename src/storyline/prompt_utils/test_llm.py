from zsp_llm_client.prompt_runner import PromptRunner
import argparse

from .clean_response import strip_think_tags


def main(prompt_template_path, input_content, models):
    """Run a prompt using the high‑level :class:`PromptRunner`.

    Reads the input content, optionally applies a pre‑processor, and writes the
    cleaned response to stdout. Delegates rate‑limiting and model
    selection to the external client library.
    """
    runner = PromptRunner()
    response = runner.run(
        prompt_template_path,
        input_content,
        models=models,
    )
    response = strip_think_tags(response)
    print(response)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run a prompt via zsp-llm-client.')
    parser.add_argument('--prompt_template_path', default='templates/simple_prompt.txt', help='Prompt template path')
    parser.add_argument('--input_file_path', help='Input file path (optional)')
    parser.add_argument('--input_text', help='Input text directly (optional)')
    parser.add_argument(
        '--models',
        type=str,
        default='qwen3-4b',
        help='Comma-separated list of models in preference order',
    )

    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(',') if m.strip()]

    input_content = args.input_text
    if not input_content and args.input_file_path:
        with open(args.input_file_path, 'r', encoding='utf-8', errors='replace') as f:
            input_content = f.read()

    if not input_content:
        parser.error('Either --input_text or --input_file_path must be provided')

    main(args.prompt_template_path, input_content, models)
