# Rate Limiter

The **zsp‑llm‑client** package ships a lightweight, file‑backed rate‑limiter that
prevents exceeding per‑model request quotas. It is used by the prompt runner
and can also be accessed directly.

## Features

* **Sliding‑window** enforcement – guarantees a model never exceeds its
  `requests_per_minute` limit.

## Usage

### Default model config

```python
from zsp_llm_client.rate_limiter import RateLimiter, get_default_rate_limiter

limiter = get_default_rate_limiter()

# How long to wait (ms) before calling a model
delay = limiter.next_call_delay("openrouter/openai/gpt-oss-120b:free")

# Choose the best model from a preference‑ordered list
best = limiter.best_model(["openrouter/openai/gpt-oss-120b:free", "qwen35-35b-a3b", "qwen35-9b"])
```

### Manual model initialization

```python
from zsp_llm_client.rate_limiter import RateLimiter

limiter = RateLimiter(limits={"openrouter/openai/gpt-oss-120b:free": 60, "qwen35-35b-a3b": 1000, "qwen35-9b": 1000})

# How long to wait before calling a model (ms)
delay = limiter.next_call_delay("openrouter/openai/gpt-oss-120b:free")

# Choose the best model from a preference‑ordered list
best = limiter.best_model(["openrouter/openai/gpt-oss-120b:free", "qwen35-35b-a3b", "qwen35-9b"])
```

