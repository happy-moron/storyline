# Thinking budget tokens

This is a per-request reasoning budget which llama.cpp supports.

You can include this in the request body (`{"thinking_budget_tokens": N}`) as long as you haven't specified a budget on the command-line.

## Usage via zsp-llm-client

Pass `extra_options` to `PromptRunner.run()` or `.run_from_file()`:

```python
runner = PromptRunner()
response = runner.run(
    "prompts/translate.md",
    input_text,
    models=["local-llamacpp"],
    extra_options={"thinking_budget_tokens": 4096},
)
```

The `extra_options` dict is sent as `extra_body` in the OpenAI-compatible API request. llama.cpp reads `thinking_budget_tokens` directly from the request body.

## Relevant code

llama.cpp/tools/server/server-common.cpp

Lines 1107 to 1119 in 0fcb376
```
 { 
     int reasoning_budget = opt.reasoning_budget; 
     if (reasoning_budget == -1 && body.contains("thinking_budget_tokens")) { 
         reasoning_budget = json_value(body, "thinking_budget_tokens", -1); 
     } 
  
     if (!chat_params.thinking_end_tag.empty()) { 
         llama_params["reasoning_budget_tokens"] = reasoning_budget; 
         llama_params["reasoning_budget_start_tag"] = chat_params.thinking_start_tag; 
         llama_params["reasoning_budget_end_tag"] = chat_params.thinking_end_tag; 
         llama_params["reasoning_budget_message"] = opt.reasoning_budget_message; 
     } 
 } 
```