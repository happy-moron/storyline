# Overview

This repository provides a lightweight **Flask** server that exposes HTTP
endpoints for the **Qwen3‑TTS** library.  The server loads a TTS model lazily
and returns generated audio as a base64‑encoded WAV payload, making it easy to
integrate into other services.

---

## Installation

The package is designed to be installed inside an existing **Qwen3‑TTS**
virtual environment.  From the root of the repository:

```bash
# Activate your Qwen3‑TTS venv (adjust the path as needed)
. ../Qwen3-TTS/.venv/bin/activate

# Install the Flask wrapper in editable mode
pip install -e .
```

The `pyproject.toml` defines a console script entry point, so after installation
you can start the server with:

```bash
qwen3-tts-flask
```

---

## Environment variables

The server is configured via ``config.toml`` bundled with the package.  Any setting
can be overridden with an environment variable using the ``QWEN3_TTS_`` prefix.

| Variable | Description | Default |
|----------|-------------|---------|
| `QWEN3_TTS_SERVER_HOST` | Host address to bind to. | `0.0.0.0` |
| `QWEN3_TTS_SERVER_PORT` | Port to listen on. | `11433` |
| `QWEN3_TTS_MODEL_MANAGEMENT_MAX_LOADED_MODELS` | Maximum number of models to keep in memory simultaneously. | `1` |
| `QWEN3_TTS_MODEL_MANAGEMENT_AUTO_UNLOAD_IDLE` | Whether to automatically unload idle models when the limit is reached. | `false` |
| `QWEN3_TTS_DEVICE_DEVICE_MAP` | Device map passed to ``from_pretrained`` (e.g. ``cuda:0``, ``auto``). | `cuda:0` |
| `QWEN3_TTS_DEVICE_DTYPE` | Torch dtype for the model (`float16`, `bfloat16`, …). | `float16` |
| `QWEN3_TTS_DEVICE_ATTN_IMPLEMENTATION` | Attention backend (`sdpa`, `eager`, …). | `sdpa` |
| `QWEN3_TTS_MODELS_VOICE_DESIGN` | Model ID/path for the voice-design model. | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` |
| `QWEN3_TTS_MODELS_CUSTOM_VOICE` | Model ID/path for the custom-voice model. | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` |
| `QWEN3_TTS_MODELS_VOICE_CLONE` | Model ID/path for the voice-clone model. | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` |
| `QWEN3_TTS_MODELS_ALIGNER` | Model ID/path for the ForcedAligner model. | `Qwen/Qwen3-ForcedAligner-0.6B` |

### Model Caching

All models (TTS and ASR ForcedAligner) are loaded via HuggingFace's ``from_pretrained()`` and
cached in the standard HuggingFace cache directory:

| OS | Default Cache Location |
|----|------------------------|
| Linux | ``~/.cache/huggingface/hub/`` |
| macOS | ``~/Library/Caches/huggingface/hub/`` |
| Windows | ``%USERPROFILE%\.cache\huggingface\hub\`` |

To use a different cache directory, set the ``HF_HOME`` or ``HUGGINGFACE_HUB_CACHE``
environment variable before starting the server:

```bash
export HF_HOME=/path/to/custom/cache
qwen3-tts-flask
```

Models are downloaded once on first use and reused across server restarts.

### Model Management

The server manages two independent model classes, each with its own VRAM slot:

| Class | Models | VRAM (fp16) | Max Loaded |
|-------|--------|-------------|-----------|
| ``tts-1.7b`` | ``voice_design``, ``custom_voice``, ``voice_clone`` | ~3.4 GiB | 1 |
| ``aligner-0.6b`` | ``aligner`` | ~1.7 GiB | 1 |

The aligner has its own dedicated slot — it is never evicted by a TTS model
and vice versa.  Within the ``tts-1.7b`` class, an LRU (Least Recently Used)
strategy evicts the oldest TTS model when the limit is reached.  A global
request lock serializes all model loading and generation operations,
preventing concurrent loads that could cause OOM errors.

To manually manage models, use the following endpoints:
- `GET /models` - List all models and their status
- `POST /models/{type}/load` - Load a model (idempotent)
- `POST /models/{type}/unload` - Unload a model (idempotent)

The load and unload operations are idempotent - calling them repeatedly with the same model type will return the appropriate status code without error.

### Model Support

| Endpoint | Supported Models |
|----------|------------------|
| `/custom_voice` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`, `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` |
| `/voice_design` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` |
| `/voice_clone` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`, `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`, `Qwen/Qwen3-TTS-12Hz-1.7B-Base` |
| `/forced_aligner` | `Qwen/Qwen3-ForcedAligner-0.6B` |

---

## Running the server

The server listens on ``0.0.0.0:11433`` by default.  You can override the host
or port via environment variables:

```bash
# Default start (host 0.0.0.0, port 11433)
qwen3-tts-flask

# Custom host/port via environment
QWEN3_TTS_SERVER_HOST=127.0.0.1 QWEN3_TTS_SERVER_PORT=5000 qwen3-tts-flask
```

---

## API Endpoints

All endpoints accept **JSON** payloads and return a JSON response containing a
base64‑encoded ``audio`` field.

### Health check
``GET /health`` – Returns ``{"status": "ok"}``.

### Custom voice

Custom voice uses one of Qwen3's pre-trained voices.

| Speaker | Voice Description  |  Native language |
| --- | --- | --- |
| Vivian | Bright, slightly edgy young female voice. | Chinese |
| Serena | Warm, gentle young female voice. | Chinese |
| Uncle_Fu | Seasoned male voice with a low, mellow timbre. | Chinese |
| Dylan | Youthful Beijing male voice with a clear, natural timbre. | Chinese (Beijing Dialect) |
| Eric | Lively Chengdu male voice with a slightly husky brightness. | Chinese (Sichuan Dialect) |
| Ryan | Dynamic male voice with strong rhythmic drive. | English |
| Aiden | Sunny American male voice with a clear midrange. | English |
| Ono_Anna | Playful Japanese female voice with a light, nimble timbre. | Japanese |
| Sohee | Warm Korean female voice with rich emotion. | Korean |

Sample Instructs:
"Speak in an incredulous tone, but with a hint of panic beginning to creep into your voice."
"Very happy."

``POST /custom_voice`` – Parameters: ``text``, ``language``, ``speaker``, optional ``instruct``.

#### Batch Inference

Batch inference is supported by passing all parameters as lists. All lists must have the same length.

**Single inference (backward compatible):**

```bash
curl -X POST http://localhost:11433/custom_voice \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello world",
    "language": "English",
    "speaker": "Ryan",
    "instruct": ""
  }'
```

**Batch inference:**

```bash
curl -X POST http://localhost:11433/custom_voice \
  -H "Content-Type: application/json" \
  -d '{
    "text": ["Hello world", "How are you?"],
    "language": ["English", "English"],
    "speaker": ["Ryan", "Aiden"],
    "instruct": ["", "Very happy"]
  }'
```

**Batch response:**

```json
{
  "audio": ["base64_encoded_audio_1", "base64_encoded_audio_2"]
}
```

**Error handling:** If the lengths of ``text``, ``language``, and ``speaker`` don't match, the endpoint returns a 400 error with a message about mismatched lengths. If ``instruct`` is provided as a list, it must match the length of ``text``.

### Voice design

Generates audio with a custom voice designed from natural language instructions.

``POST /voice_design`` – Parameters: ``text``, ``language``, optional ``instruct``.

Sample Instructs:
"Male, 17 years old, tenor range, gaining confidence - deeper breath support now, though vowels still tighten when nervous"

### Voice clone

Generates audio by cloning a voice using a reference audio/text pair.

``POST /voice_clone`` – Parameters: ``text``, ``language``, ``ref_audio`` (URL or base64), ``ref_text``.

### Model management

#### GET /models

Get the status of all models.

**Response:**
```json
{
  "models": [
    {
      "model_type": "custom_voice",
      "loaded": true,
      "last_used": 1705270400.123
    },
    {
      "model_type": "voice_design",
      "loaded": false,
      "last_used": null
    },
    {
      "model_type": "voice_clone",
      "loaded": false,
      "last_used": null
    },
    {
      "model_type": "aligner",
      "loaded": false,
      "last_used": null
    }
  ]
}
```

#### POST /models/{model_type}/load

Manually load a model (idempotent).

**Request Parameters:**
- `model_type`: ``"custom_voice"`` | ``"voice_design"`` | ``"voice_clone"`` | ``"aligner"``

**Response (already loaded):**
```json
{
  "status": 200,
  "model_type": "custom_voice",
  "loaded": true,
  "message": "Model already loaded"
}
```

**Response (newly loaded):**
```json
{
  "status": 201,
  "model_type": "custom_voice",
  "loaded": true,
  "message": "Model loaded successfully"
}
```

**Error Response (max limit reached):**
```json
{
  "status": 422,
  "model_type": "custom_voice",
  "loaded": false,
  "message": "Cannot load custom_voice: maximum of 1 models are currently loaded."
}
```

**Status Codes:**
- 200: Model already loaded
- 201: Model newly loaded
- 400: Invalid model_type
- 422: Cannot load (max limit reached)

#### POST /models/{model_type}/unload

Manually unload a model (idempotent).

**Request Parameters:**
- `model_type`: ``"custom_voice"`` | ``"voice_design"`` | ``"voice_clone"`` | ``"aligner"``

**Response (already unloaded — never loaded):**
```json
{
  "status": 404,
  "model_type": "custom_voice",
  "unloaded": true,
  "message": "Model not found (never loaded)"
}
```

**Response (successfully unloaded):**
```json
{
  "status": 204,
  "model_type": "custom_voice",
  "unloaded": true,
  "message": "Model unloaded successfully"
}
```

**Status Codes:**
- 204: Model successfully unloaded
- 400: Invalid model_type
- 404: Model not found (never loaded)

### Forced Aligner

Runs word-level forced alignment on an audio file and known reference text
using the Qwen3-ForcedAligner-0.6B model.

``POST /forced_aligner`` — Parameters: ``audio`` (base64 WAV), ``text``, ``language``.

```bash
curl -X POST http://localhost:11433/forced_aligner \
  -H "Content-Type: application/json" \
  -d '{
    "audio": "'"$(base64 -w0 audio.wav)"'",
    "text": "The quick brown fox.",
    "language": "English"
  }'
```

**Response:**

```json
{
  "words": [
    {"text": "The",   "start_time": 0.12, "end_time": 0.34},
    {"text": "quick", "start_time": 0.36, "end_time": 0.58},
    {"text": "brown", "start_time": 0.60, "end_time": 0.85},
    {"text": "fox",   "start_time": 0.88, "end_time": 1.02}
  ]
}
```

---

## Example curl commands

```bash
# Custom voice
curl -X POST http://localhost:11433/custom_voice \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "language": "English", "speaker": "Ryan", "instruct": ""}'

# Voice design
curl -X POST http://localhost:11433/voice_design \
  -H "Content-Type: application/json" \
  -d '{"text": "Good morning", "language": "English", "instruct": "cheerful"}'

# Voice clone
curl -X POST http://localhost:11433/voice_clone \
  -H "Content-Type: application/json" \
  -d '{"text": "How are you?", "language": "English", "ref_audio": "http://example.com/ref.wav", "ref_text": "Reference text"}'
```

---

## Testing

Unit tests are located in the ``tests`` directory and can be run with:

```bash
pytest -q
```

The tests monkey‑patch the model loading to avoid downloading large weights.

---
