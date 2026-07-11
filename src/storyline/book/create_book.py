import os
import argparse
import re
import tomllib
import logging
from pathlib import Path

# Absolute imports for internal modules
from storyline.book.parse_pipe_format import (
    parse_source_pipe,
    parse_source_file,
    parse_tokenized_file,
    parse_tokenized_pipe,
)
from storyline.book.cjk_punct import strip as strip_cjk_punct, reinsert as reinsert_cjk_punct, format_compact
from storyline.book.split_text import split_text
from storyline.prompt_utils.run_prompt import run_prompt
from storyline.book.create_custom_dict import process_json_file, load_dictionary
from storyline.services.manager import ServiceManager
from storyline.audio.audiobook_gen_qwen3 import process_json_to_audio
from storyline.book.tokenization_repair import (
    validate_full,
    build_repair_input,
    apply_repairs,
)
from zsp_llm_client.prompt_runner import PromptRunner

def _resolve_profile(task_profiles, task, default_profile):
    if task_profiles:
        return task_profiles.get(task, default_profile)
    return default_profile


def create_book(input_text, author, max_chunks, skip_simplify, models, skip_audio, audio_profile="default", service_manager=None, task_profiles=None, llm_provider="local"):

    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%H:%M:%S', level=logging.INFO)
    log = logging.getLogger(__name__)

    book = os.path.splitext(os.path.basename(input_text))[0]

    base_dir = f"books/{author}/{book}"
    split_source_dir = f"{base_dir}/split/source"
    pipe_source_dir = f"{base_dir}/pipe/source"
    pipe_token_dir = f"{base_dir}/pipe/tokenized"
    audio_dir = f"{base_dir}/audio"
    audiobook_dir = f"/home/zspdude/temp/audio/{book}"
    
    # Manage local LLM service (only when provider is "local")
    if service_manager and llm_provider == "local":
        default_profile = task_profiles.get("default", task_profiles.get("translate")) if task_profiles else None
        warmup_profile = _resolve_profile(task_profiles, 'translate', default_profile)
        service_manager.ensure_llm_profile(warmup_profile)
        # First prompt compiles CUDA graphs (can take minutes on cold start).
        # Warm up now so real work doesn't time out.
        log.info("Warming up LLM (first call compiles CUDA graphs, may take a while)...")
        warmup_runner = PromptRunner()
        warmup_runner.run(
            "prompts/warmup.txt",
            "Hello.",
            models=models,
        )
        log.info("LLM warmup complete.")

    # 1 - Split the text
    split_text(input_text, split_source_dir)
    
    input_files = list(Path(split_source_dir).glob('*.txt'))

    processed = 0
    ordered_files = natural_sort_filenames(input_files)
 
    dictionary = load_dictionary("dict/custom_dict.json")
    for source_file in ordered_files:
        processed += 1
        if processed > int(max_chunks):
            break
        
        source_file_stem = os.path.splitext(os.path.basename(source_file))[0]

        log.info("processing %s", source_file_stem)
        
        # 2 - Translate the text (pipe format stored directly)
        os.makedirs(pipe_source_dir, exist_ok=True)
        source_txt_path = Path(pipe_source_dir) / Path(str(source_file_stem) + ".txt")
        if not source_txt_path.exists():
            if service_manager and llm_provider == "local":
                default_profile = task_profiles.get("default", task_profiles.get("translate")) if task_profiles else None
                translate_profile = _resolve_profile(task_profiles, 'translate', default_profile)
                service_manager.ensure_llm_profile(translate_profile)

            if skip_simplify:
                translate_prompt = "prompts/translate.md"
            else:
                translate_prompt = "prompts/translate_and_simplify.md"

            max_retries = 3
            for attempt in range(max_retries):
                run_prompt(translate_prompt, source_file, source_txt_path,
                           models=models)
                if skip_simplify:
                    # Straight translation: LLM outputs Chinese only, no |||.
                    # Append ||| after each line for downstream compatibility.
                    chinese_lines = Path(source_txt_path).read_text(encoding="utf-8").strip().split("\n")
                    pipe_text = "\n".join(line.strip() + "|||" for line in chinese_lines if line.strip())
                    Path(source_txt_path).write_text(pipe_text, encoding="utf-8")
                try:
                    sentences = parse_source_file(source_txt_path)
                    break
                except ValueError:
                    if attempt < max_retries - 1:
                        log.info("  Translation parse failed (attempt %d/%d), retrying...", attempt+1, max_retries)
                        os.remove(source_txt_path)
                    else:
                        raise
            log.info("  -> wrote %d sentence pairs to %s", len(sentences), source_txt_path)

        # 4 - Tokenize the translated (Chinese-only input, compact pipe format)
        os.makedirs(pipe_token_dir, exist_ok=True)
        token_txt_path = Path(pipe_token_dir) / Path(str(source_file_stem) + ".txt")
        if not token_txt_path.exists():
            # Extract Chinese sentences from source pipe
            source_sentences = parse_source_file(source_txt_path)
            # Strip whitespace from Chinese — spaces between words are not
            # meaningful for POS annotation (translation artifact).
            chinese_sentences = [
                "".join(item["chinese"].split()) for item in source_sentences
            ]

            # Strip CJK punctuation to save tokens (LLM doesn't need to annotate it)
            stripped_sentences = []
            punct_maps = []  # per-sentence position maps for re-insertion
            for ch in chinese_sentences:
                stripped, positions = strip_cjk_punct(ch)
                stripped_sentences.append(stripped)
                punct_maps.append(positions)

            chinese_input_path = Path(pipe_token_dir) / Path(str(source_file_stem) + "_input.txt")
            with open(chinese_input_path, "w", encoding="utf-8") as f:
                f.write("\n".join(stripped_sentences))

            if service_manager and llm_provider == "local":
                default_profile = task_profiles.get("default", task_profiles.get("tokenize")) if task_profiles else None
                tokenize_profile = _resolve_profile(task_profiles, 'tokenize', default_profile)
                service_manager.ensure_llm_profile(tokenize_profile)

            max_retries = 3
            for attempt in range(max_retries):
                run_prompt("prompts/tokenize.txt", chinese_input_path, token_txt_path,
                           models=models)

                raw_text = Path(token_txt_path).read_text(encoding="utf-8")
                try:
                    tokenized = parse_tokenized_file(token_txt_path, expected_chinese=stripped_sentences)
                    break
                except ValueError:
                    if attempt >= max_retries - 1:
                        raise

                    # Parse without validation to identify specific errors
                    try:
                        tokenized_no_val = parse_tokenized_file(token_txt_path)
                        raw_lines = raw_text.strip().split("\n")
                        report = validate_full(stripped_sentences, tokenized_no_val, raw_lines)
                    except ValueError:
                        log.info("  Tokenization unparseable (attempt %d/%d), retrying...", attempt+1, max_retries)
                        os.remove(token_txt_path)
                        continue

                    if report.is_clean:
                        tokenized = tokenized_no_val
                        break

                    if report.has_structural_errors:
                        log.info("  Tokenization: structural errors, full retry (attempt %d/%d)", attempt+1, max_retries)
                        os.remove(token_txt_path)
                        continue

                    # Targeted repair — send only bad sentences to LLM
                    log.info("  Tokenization: %d/%d errors, attempting repair...", len(report.bad_indices), len(stripped_sentences))
                    repair_input = build_repair_input(report, stripped_sentences, raw_lines)
                    repair_input_path = Path(pipe_token_dir) / Path(str(source_file_stem) + "_repair_input.txt")
                    repair_output_path = Path(pipe_token_dir) / Path(str(source_file_stem) + "_repair_output.txt")

                    with open(repair_input_path, "w", encoding="utf-8") as f:
                        f.write(repair_input)

                    run_prompt("prompts/fix_tokenization.txt", repair_input_path, repair_output_path,
                               models=models)

                    repaired_text = Path(repair_output_path).read_text(encoding="utf-8")
                    repaired_lines = apply_repairs(raw_lines, repaired_text, report.bad_indices)

                    with open(token_txt_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(repaired_lines) + "\n")

                    os.remove(repair_input_path)
                    os.remove(repair_output_path)

                    try:
                        tokenized = parse_tokenized_file(token_txt_path, expected_chinese=stripped_sentences)
                        log.info("  Repair successful!")
                        break
                    except ValueError:
                        log.info("  Repair failed, full retry (attempt %d/%d)", attempt+1, max_retries)
                        os.remove(token_txt_path)
                        continue
            os.remove(chinese_input_path)

            # Re-insert CJK punctuation from original source text
            for i, sentence_data in enumerate(tokenized):
                original = chinese_sentences[i]
                sentence_data["t"] = reinsert_cjk_punct(
                    sentence_data["t"], punct_maps[i], original
                )

            # Write corrected file (with punct tokens) so downstream consumers
            # see the complete annotated text
            with open(token_txt_path, "w", encoding="utf-8") as f:
                for sentence_data in tokenized:
                    f.write(format_compact(sentence_data["t"]) + "\n")

            log.info("  -> wrote %d tokenized sentences to %s", len(tokenized), token_txt_path)

        # 5 - Generate the audio (optional)
        if not skip_audio:
            audiobook_mp3_path = Path(audiobook_dir) / Path(str(source_file_stem) + ".mp3")
            os.makedirs(audio_dir, exist_ok=True)
            os.makedirs(audiobook_dir, exist_ok=True)
            if not audiobook_mp3_path.exists():
                if service_manager:
                    service_manager.start_if_needed('tts')
                
                process_json_to_audio(
                    source_txt_path,
                    f"{audiobook_dir}/{source_file_stem}.mp3",
                    standalone_file=f"{audio_dir}/{source_file_stem}",
                    profile_key=audio_profile,
                )
                
                if service_manager:
                    service_manager.stop_if_running('tts')
                    if llm_provider == "local":
                        default_profile = task_profiles.get("default", task_profiles.get("dictionary")) if task_profiles else None
                        dict_profile = _resolve_profile(task_profiles, 'dictionary', default_profile)
                        service_manager.ensure_llm_profile(dict_profile)

        # 6 - Update the dictionary
        if service_manager and llm_provider == "local":
            default_profile = task_profiles.get("default", task_profiles.get("dictionary")) if task_profiles else None
            dict_profile = _resolve_profile(task_profiles, 'dictionary', default_profile)
            service_manager.ensure_llm_profile(dict_profile)

        process_json_file(token_txt_path, dictionary, "dict/custom_dict.json",
                          "prompts/create_single_dictionary_entry.txt",
                          models=models)

    # Stop local LLM service if we were managing it
    if service_manager and llm_provider == "local":
        service_manager.stop('llm')

def natural_sort_filenames(filenames):
    """
    Sort filenames of the format <prefix>_<sequenceNum>.ext in natural numerical order.
    
    Args:
        filenames: List of filenames to sort
        
    Returns:
        List of filenames sorted by their numerical sequence
    """
    def extract_number(filename):
        # Extract the numerical part between _ and .ext
        match = re.search(r'_(\d+)\.', str(filename))
        if match:
            return int(match.group(1))
        return 0  # Default if no number found
    
    return sorted(filenames, key=extract_number)

if __name__ == '__main__':
    # Initialize ServiceManager
    service_manager = ServiceManager()
    
    parser = argparse.ArgumentParser(description="Generate an audio book")

    # The models are now defined centrally in ``llms_for_tasks.toml``. The CLI
    # option is retained for backward compatibility but will be overridden by the
    # configuration file's ``default`` profile.
    parser.add_argument(
        "-m",
        "--models",
        type=str,
        default="",
        help="Comma-separated list of models (overridden by config file)",
    )
    parser.add_argument('-a', '--author', required=True, help='author name')
    parser.add_argument('-i', '--input', required=True, help='plaintext input')
    parser.add_argument('--skip-simplify', default=False, action=argparse.BooleanOptionalAction, dest='skip_simplify')
    parser.add_argument('--max-chunks', dest='max_chunks', type=int, default=500, help='max number of chunks')
    parser.add_argument('--skip-audio', dest='skip_audio', action=argparse.BooleanOptionalAction, default=False, help='Skip audio generation')
    parser.add_argument('--audio-profile', default='default', help='Audio profile from audio.toml (e.g. default, voice-clone)')
    args = parser.parse_args()
    
    # Load model profiles from configuration file
    config_path = Path(__file__).parent.parent / "config" / "llms_for_tasks.toml"
    try:
        with config_path.open("rb") as f:
            cfg = tomllib.load(f)
        llm_cfg = cfg.get("llm", {})
        llm_provider = llm_cfg.get("provider", "local")
        model_id = llm_cfg.get("model_id", "local-llamacpp")
        models = [model_id]
        default_profile = cfg.get("default", {}).get("profile")
        task_profiles = {
            task: cfg.get(task, {}).get("profile", default_profile)
            for task in ("translate", "tokenize", "dictionary")
        }
    except Exception:
        llm_provider = "local"
        models = ["local-llamacpp"]
        task_profiles = {}
    
    try:
        create_book(
            args.input,
            args.author,
            args.max_chunks,
            args.skip_simplify,
            models,
            args.skip_audio,
            audio_profile=args.audio_profile,
            service_manager=service_manager,
            task_profiles=task_profiles,
            llm_provider=llm_provider,
        )
    finally:
        # Ensure local LLM service is stopped
        if llm_provider == "local":
            service_manager.stop('llm')
