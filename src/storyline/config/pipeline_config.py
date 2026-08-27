import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_DIR = Path(__file__).resolve().parent


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*.  Returns a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _unnest(d: dict) -> dict:
    """Flatten a two-level nested dict: {a: {b: v}} -> {'a.b': v}.

    Only flattens one level — keeps deeper nesting intact.  This is needed
    because TOML dotted keys like ``paths.books_dir`` produce a nested dict
    but CLI overrides and profile sections use flat dotted keys.
    """
    result: dict[str, Any] = {}
    for key, value in d.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                result[f"{key}.{subkey}"] = subvalue
        else:
            result[key] = value
    return result


def _nest(d: dict) -> dict:
    """Inverse of _unnest: {'a.b': v} -> {a: {b: v}}."""
    result: dict[str, Any] = {}
    for key, value in d.items():
        parts = key.split(".", 1)
        if len(parts) == 2:
            result.setdefault(parts[0], {})[parts[1]] = value
        else:
            result[key] = value
    return result


@dataclass
class PipelineConfig:
    """Central configuration for the create_book pipeline.

    Loaded from ``pipeline.toml``, merged with a named profile if
    ``--profile`` is given, then overridden by CLI arguments.
    """

    # -- Paths --
    books_dir: str = "books"
    audiobook_dir: str = "/home/zspdude/temp/audio"
    dict_file: str = "dict/custom_dict.json"
    prompts: dict[str, str] = field(default_factory=lambda: {
        "warmup": "prompts/warmup.txt",
        "simplify": "prompts/simplify.md",
        "chunk": "prompts/tts_chunking_prompt_single_narrator.md",
        "translate": "prompts/translate.md",
        "tokenize": "prompts/tokenize.txt",
        "fix_tokenization": "prompts/fix_tokenization.txt",
        "dict_entry": "prompts/create_single_dictionary_entry.txt",
        "podcast_vocab": "prompts/podcast-vocab.md",
        "podcast_script": "prompts/podcast-script.md",
        "podcast_fix_script": "prompts/podcast-fix-script.md",
        "podcast_fix_dialogue": "prompts/podcast-fix-dialogue.md",
        "podcast_fix_voice_profiles": "prompts/podcast-fix-voice-profiles-script.md",
    })

    # -- Pipeline behaviour --
    max_chunks: int = 500
    split_chunk_size: int = 2000
    llm_retries: int = 3
    llm_timeout_s: int = 1500
    warmup_on_start: bool = True
    skip_simplify: bool = False
    skip_audio: bool = False
    audio_profile: str = "default"
    audio_use_instruct: bool | None = None

    # -- Dictionary --
    punctuation_skip: list[str] = field(default_factory=lambda: [
        ",", ".", "?", "!", "，", "。", "？", "！",
        '"', "“", "”", "、",
    ])

    # -- LLM (from llms_for_tasks.toml) --
    models: list[str] = field(default_factory=lambda: ["local-llamacpp"])
    llm_provider: str = "local"
    task_profiles: dict[str, str] = field(default_factory=dict)
    task_thinking_budget: dict[str, int | None] = field(default_factory=dict)

    # -- Profile name (for reference) --
    profile_name: str | None = None

    @classmethod
    def from_files_and_args(
        cls,
        args: Any | None = None,
        *,
        profile_name: str | None = None,
    ) -> "PipelineConfig":
        """Load config from TOML files, apply profile, then CLI overrides.

        *args* is an ``argparse.Namespace`` or any object with attributes
        matching CLI flags.  Values on *args* take highest priority.
        """
        # 1. Load pipeline.toml defaults
        pipeline_path = CONFIG_DIR / "pipeline.toml"
        with pipeline_path.open("rb") as f:
            raw = tomllib.load(f)
        defaults = raw.get("defaults", {})
        profiles = raw.get("profiles", {})

        # 2. Merge profile overrides
        merged = copy.deepcopy(defaults)
        if profile_name and profile_name in profiles:
            merged = _deep_merge(merged, profiles[profile_name])

        # 3. Unnest for flat dotted-key lookup
        flat = _unnest(merged)

        # 4. Build config instance
        cfg = cls()
        cfg.profile_name = profile_name

        # Paths
        cfg.books_dir = flat.get("paths.books_dir", cfg.books_dir)
        cfg.audiobook_dir = flat.get("paths.audiobook_dir", cfg.audiobook_dir)
        cfg.dict_file = flat.get("paths.dict_file", cfg.dict_file)
        prompts_flat = {
            k.split("paths.prompts.", 1)[1]: v
            for k, v in flat.items()
            if k.startswith("paths.prompts.")
        }
        for name, default_val in cfg.prompts.items():
            cfg.prompts[name] = prompts_flat.get(name, default_val)

        # Pipeline
        cfg.max_chunks = int(flat.get("pipeline.max_chunks", cfg.max_chunks))
        cfg.split_chunk_size = int(flat.get("pipeline.split_chunk_size", cfg.split_chunk_size))
        cfg.llm_retries = int(flat.get("pipeline.llm_retries", cfg.llm_retries))
        cfg.llm_timeout_s = int(flat.get("pipeline.llm_timeout_s", cfg.llm_timeout_s))
        cfg.warmup_on_start = bool(flat.get("pipeline.warmup_on_start", cfg.warmup_on_start))
        cfg.skip_simplify = bool(flat.get("pipeline.skip_simplify", cfg.skip_simplify))
        cfg.skip_audio = bool(flat.get("pipeline.skip_audio", cfg.skip_audio))
        cfg.audio_profile = str(flat.get("pipeline.audio_profile", cfg.audio_profile))

        # Dictionary
        cfg.punctuation_skip = flat.get("dictionary.punctuation_skip", cfg.punctuation_skip)

        # 5. Load LLM config
        cfg._load_llm_config()

        # 6. Apply CLI overrides (highest priority)
        if args is not None:
            cfg._apply_cli_overrides(args)

        return cfg

    def _load_llm_config(self) -> None:
        llm_path = CONFIG_DIR / "llms_for_tasks.toml"
        try:
            with llm_path.open("rb") as f:
                llm_cfg = tomllib.load(f)
            llm = llm_cfg.get("llm", {})
            self.llm_provider = llm.get("provider", "local")
            model_id = llm.get("model_id", "local-llamacpp")
            self.models = [model_id]
            default = llm_cfg.get("default", {})
            default_profile = default.get("profile")
            default_thinking = default.get("thinking_budget_tokens")
            tasks = (
                "translate", "tokenize", "dictionary",
                "podcast_vocab", "podcast_script",
                "podcast_fix_script", "podcast_fix_dialogue",
                "podcast_fix_voice_profiles",
            )
            self.task_profiles = {
                task: llm_cfg.get(task, {}).get("profile", default_profile)
                for task in tasks
            }
            self.task_thinking_budget = {
                task: llm_cfg.get(task, {}).get("thinking_budget_tokens", default_thinking)
                for task in tasks
            }
        except Exception:
            self.llm_provider = "local"
            self.models = ["local-llamacpp"]
            self.task_profiles = {}
            self.task_thinking_budget = {}

    def _apply_cli_overrides(self, args: Any) -> None:
        for attr in (
            "max_chunks", "skip_simplify", "skip_audio",
            "audio_profile", "audio_use_instruct",
        ):
            if hasattr(args, attr):
                val = getattr(args, attr)
                if val is not None:
                    setattr(self, attr, val)

        # models from CLI replaces the list (handled separately to guard
        # against argparse default="" overwriting the config-loaded value)
        if hasattr(args, "models") and args.models:
            if isinstance(args.models, str):
                self.models = [m.strip() for m in args.models.split(",") if m.strip()]
            else:
                self.models = list(args.models)

    def resolve_prompt(self, prompt_name: str) -> str:
        """Return the path for a named prompt, or raise KeyError."""
        return self.prompts[prompt_name]

    def resolve_thinking_budget(self, task: str) -> int | None:
        default = self.task_thinking_budget.get("default")
        return self.task_thinking_budget.get(task, default)

    # ------------------------------------------------------------------
    # Convenience computed properties (used by create_book)
    # ------------------------------------------------------------------
    def book_dir(self, author: str, book: str) -> str:
        return os.path.join(self.books_dir, author, book)

    def audiobook_output_dir(self, book: str) -> str:
        return os.path.join(self.audiobook_dir, book)