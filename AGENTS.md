# Project Overview

This project centers on generating language learning materials. It relies on LLMs for translation and TTS models for audio generation. The main scripting language is python.

It implements:

* An HTML/JS/CSS webapp for reading e-books with audio
* A pipeline for chunking, translating and generating audio and dictionary entries for the reader
* Supporting scripts and LLM prompts

## Notes on state

Some areas of the project represent earlier experimental work or ideas which are proof of concept or not fully implemented. 

dict/wordlists, prompts/flashcards - used for generating lists of word for flashcard creation via image-gen (not implemented)
dict/hsk*, prompts/grammar, prompts/dict - investigation work for augmenting the reader app with detected grammar points

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

Imagegen is planned in the future. There's a ComfyUI server.

It runs on http://127.0.0.1:11434 and can be started/stopped by systemctl.

```bash
systemctl --user start comfyui
systemctl --user stop comfyui
```

# Python Install

All python work for this should be done within a local virtual environment '.venv'. It is safe to assume this exists.

If running python commands, make sure to run ". .venv/bin/activate" prior.

# Testing

## Unit Testing

Testing uses 'pytest'

## "RealWorld" test scenarios

To distinguish from ambiguous understandings of 'integration' or 'e2e' tests, "RealWorld" tests have the following characteristics:

* They MUST NOT use mocks or stubs
* They MUST use real infrastructure (e.g. local or remote llms, GPU hardware etc)
* They MUST test E2E using existing CLIs or entrypoints
* They MUST be maintained as a separate suite from other unit tests
* They SHOULD maintain state responsibly and be re-runnable without intervention (e.g. cleaning up before starting, shutting down services when finished, isolation of artifacts etc.)
* They SHOULD be relatively few in number and high impact in design
* They SHOULD be reasonably performant (e.g. small texts or audio samples), single tests not taking longer than 5-10 minutes. 
* They MAY use either pytest or custom scripts (python, bash) as runners/harness


# Code Guidelines

Write SOLID code.
Keep algorithmic code and logic clearly separate in modules that are easy to unit test.
Maintain the unix philosophy of small units which do one thing and which can be composed together.
Build small-self contained units first with proper testing.
Take a TDD approach and define test cases first, design the harness needed to run those tests, and then implement. Implement a few test cases to get the  happy-path implementation up and then implement the remainder of the tests to get proper coverage across non-happypath cases.

## Code should be self-documenting

Docstrings are almost always redundant, waste token counts and hamper LLM performance, and add next to no value to the code. 

DON'T write code with docstrings or other useless comments. 

Comments should only be used for things which are unintuitive and which can't be inferred from the code itself (TODOs, reasons and decisions for things.)
