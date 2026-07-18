# ZSP LLM Client API

This repository provides a small Python client for interacting with LLM models
while respecting per‑model rate limits. The public entry points are:

* **`PromptRunner`** – a high‑level helper that loads a prompt template, optional
  pre‑processor, applies rate‑limiting, calls the model via the `llm` library and
  returns the cleaned response.
* **`rate_limiter`** – a lightweight module that tracks calls and enforces RPM
  limits. It can be used directly for custom workflows.

---

## Quick start

```python
from zsp_llm_client.prompt_runner import PromptRunner  # [`PromptRunner`](src/zsp_llm_client/prompt_runner.py:1)

# Create a runner (rate‑limiting enabled by default)
runner = PromptRunner()

response = runner.run_from_file(
    "templates/prompt.txt",          # path to the prompt template
    "data/input.txt",                # file containing the input content
    pre_process_module="my_preprocess",  # optional module with a ``preprocess`` function
    models=["qwen3-30b-a3b"],    # list of model identifiers in preference order
)

print(response)
```

The same functionality is available from the command line via
[`src/run_prompt.py`](src/run_prompt.py:1). Use the `--no_rate_limit` flag to bypass
waiting for rate limits during testing.

---

### Extra options (e.g. `thinking_budget_tokens`)

`run()` and `run_from_file()` accept an `extra_options` dict. Its contents are
sent as `extra_body` in the underlying API request, which llama.cpp uses for
server‑side reasoning budgets:

```python
runner = PromptRunner()

response = runner.run(
    "templates/prompt.txt",
    "Translate this sentence.",
    models=["local-llamacpp"],
    extra_options={"thinking_budget_tokens": 4096},
)
```

The `extra_options` dict is forwarded verbatim. See
[`llamacpp-features.md`](llamacpp-features.md) for details on llama.cpp's
supported extra request fields.

---

## Rate‑limiter basics

The rate‑limiter is initialised automatically on import, persisting state in the
LLM logs SQLite database. For advanced use you can interact with it directly.

More details are in the dedicated rate‑limiter documentation:
[`docs/rate-limiter/README.md`](docs/rate-limiter/README.md:1).

