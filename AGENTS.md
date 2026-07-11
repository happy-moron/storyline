# Project Overview

This project centers on generating language learning materials. It relies on LLMs for translation and TTS models for audio generation. The main scripting language is python.

It implements:

* An HTML/JS/CSS webapp for reading e-books with audio
* A pipeline for chunking, translating and generating audio and dictionary entries for the reader
* Supporting scripts and LLM prompts

# Environment

This project is intended to run on a single workstation ("to work on my box"). VRAM is limited; it has an LLM, a TTS model, and an Image-gen model which run locally.
ONLY ONE of the LLM/TTS/Image-gen models can be run at a time. They are wrapped in systemctl services. 

## LLM

The LLM is accessed via a wrapping client library (external_docs/zsp-llm-client/README.md). It is an OpenAI Compatible endpoint (v1)

It runs on http://127.0.0.1:11432/v1 amd can be started/stopped via systemctl

```bash
systemctl --user start llamacpp
systemctl --user stop llamacpp
```

## TTS

The TTS model is wrapped by a custom flask server ( external_docs/qwen3-tts-flask/README.md ).

It runs on http://127.0.0.1:11433 and can be started/stopped by systemctl.

```bash
systemctl --user start qwentts
systemctl --user stop qwentts
```

## Image-gen

The Image-gen model is a custom flask server ( external_docs/qwen3-tts-flask/README.md ).

It runs on http://127.0.0.1:11434 and can be started/stopped by systemctl.

```bash
systemctl --user start qwentts
systemctl --user stop qwentts
```

# Python Install

All python work for this should be done within a local virtual environment '.venv'. It is safe to assume this exists.

If running python commands, make sure to run ". .venv/bin/activate" prior.

# Unit Testing

Testing uses 'pytest'

# Code Guidelines

## Code should be self-documenting

Docstrings are almost always redundant, waste token counts and hamper LLM performance, and add next to no value to the code. 

DON'T write code with docstrings or other useless comments. 

Comments should only be used for things which are unintuitive and which can't be inferred from the code itself (TODOs, reasons and decisions for things.)
