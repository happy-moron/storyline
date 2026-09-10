import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from storyline.logging import get_logger
from storyline.prompt_utils.run_prompt import run_prompt_with_metrics

_log = get_logger("podcast.backchain")


@dataclass
class BackchainStep:
    text: str
    is_final: bool = False


@dataclass
class BackchainResult:
    line_index: int
    original: str
    steps: list[BackchainStep]


def generate_backchains(
    dialogue_lines: list[str],
    slug: str,
    base_dir: Path,
    resolve_prompt,
    models: list[str],
    llm_timeout_s: int,
    llm_retries: int,
    extra_options: dict | None = None,
) -> list[BackchainResult]:
    if not dialogue_lines:
        return []

    base_dir = Path(base_dir)
    chunks_dir = base_dir / "chunks"
    stem = f"{slug}_1"

    input_path = chunks_dir / f"{stem}_backchain_input.txt"
    output_path = chunks_dir / f"{stem}_backchain_output.txt"

    input_text = "\n".join(dialogue_lines) + "\n"
    input_path.write_text(input_text, encoding="utf-8")

    for attempt in range(llm_retries):
        if attempt == 0:
            _log.info(
                "event=backchain_llm_call lines=%d attempt=%d",
                len(dialogue_lines), attempt + 1,
            )
            run_prompt_with_metrics(
                resolve_prompt("backchain"),
                input_path,
                output_path,
                models=models,
                timeout=llm_timeout_s,
                prompt_label="backchain",
                extra_options=extra_options,
            )

        raw_output = output_path.read_text(encoding="utf-8")
        parsed = parse_backchain_output(raw_output, dialogue_lines)
        errors = validate_backchain_output(parsed, dialogue_lines)

        if not errors:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            _log.info(
                "event=backchain_valid lines=%d attempt=%d",
                len(dialogue_lines), attempt + 1,
            )
            return parsed

        if attempt >= llm_retries - 1:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            raise ValueError(
                f"Backchain generation failed after {llm_retries} attempts: "
                f"{'; '.join(errors[:5])}"
            )

        _log.warning(
            "event=backchain_validation_errors attempt=%d errors=%s",
            attempt + 1, "; ".join(errors[:5]),
        )

        fix_input_path = chunks_dir / f"{stem}_backchain_fix_input_{attempt}.txt"
        fix_output_path = chunks_dir / f"{stem}_backchain_fix_output_{attempt}.txt"
        fix_input_text = _build_fix_input(input_text, raw_output, errors)
        fix_input_path.write_text(fix_input_text, encoding="utf-8")

        run_prompt_with_metrics(
            resolve_prompt("backchain_fix"),
            fix_input_path,
            fix_output_path,
            models=models,
            timeout=llm_timeout_s,
            prompt_label="backchain_fix",
            extra_options=extra_options,
        )

        fix_output_path.rename(output_path)
        fix_input_path.unlink(missing_ok=True)

    return []


def parse_backchain_output(raw_text: str, dialogue_lines: list[str]) -> list[BackchainResult]:
    blocks = _split_blocks(raw_text, dialogue_lines)
    results: list[BackchainResult] = []
    used_indices: set[int] = set()

    for block in blocks:
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if not lines:
            continue
        final_text = lines[-1]
        line_idx = _find_matching_index(final_text, dialogue_lines, used_indices)

        steps = []
        for i, text in enumerate(lines):
            steps.append(BackchainStep(text=text, is_final=(i == len(lines) - 1)))

        results.append(BackchainResult(
            line_index=line_idx,
            original=final_text,
            steps=steps,
        ))
        if line_idx != -1:
            used_indices.add(line_idx)

    # Sort by line_index for canonical ordering
    results.sort(key=lambda r: r.line_index)
    return results


def validate_backchain_output(
    results: list[BackchainResult],
    dialogue_lines: list[str],
) -> list[str]:
    errors: list[str] = []
    covered = set()

    for r in results:
        if r.line_index == -1:
            errors.append(
                f"Unmatched backchain final line: '{r.original[:40]}'"
            )
            continue
        if r.line_index in covered:
            errors.append(
                f"Duplicate backchain for line {r.line_index}: '{r.original[:40]}'"
            )
            continue
        covered.add(r.line_index)

        if r.original != dialogue_lines[r.line_index]:
            errors.append(
                f"Final step mismatch for line {r.line_index}: "
                f"expected '{dialogue_lines[r.line_index][:40]}', "
                f"got '{r.original[:40]}'"
            )
            continue

        for i in range(len(r.steps) - 1):
            curr = r.steps[i].text
            nxt = r.steps[i + 1].text
            if not nxt.endswith(curr):
                errors.append(
                    f"Broken chain at line {r.line_index} step {i}: "
                    f"'{nxt[:40]}' does not end with '{curr[:40]}'"
                )

    for i, line in enumerate(dialogue_lines):
        if i not in covered:
            errors.append(f"Line {i} not covered: '{line[:40]}'")

    return errors


def _split_blocks(raw_text: str, dialogue_lines: list[str] | None = None) -> list[str]:
    paragraphs = raw_text.strip().split("\n\n")
    if len(paragraphs) > 1:
        return [p.strip() for p in paragraphs if p.strip()]

    if dialogue_lines is not None and len(paragraphs) == 1:
        fallback = _split_by_dialogue_markers(paragraphs[0].strip(), dialogue_lines)
        if len(fallback) > 1:
            return fallback

    return [p.strip() for p in paragraphs if p.strip()]


def _split_by_dialogue_markers(raw_text: str, dialogue_lines: list[str]) -> list[str]:
    """Fallback: split when LLM used single newlines instead of blank lines."""
    all_lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    dialogue_set = {_normalize(l) for l in dialogue_lines}
    blocks: list[str] = []
    current_block: list[str] = []
    for line in all_lines:
        current_block.append(line)
        if _normalize(line) in dialogue_set:
            blocks.append("\n".join(current_block))
            current_block = []
    if current_block:
        blocks.append("\n".join(current_block))
    return blocks


def _find_matching_index(text: str, dialogue_lines: list[str], used_indices: set[int] | None = None) -> int:
    if used_indices is None:
        used_indices = set()
    norm_text = _normalize(text)
    for i, line in enumerate(dialogue_lines):
        if i not in used_indices and _normalize(line) == norm_text:
            return i
    return -1


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text.strip())


def fix_token_boundaries(
    results: list[BackchainResult],
    token_texts: list[list[str]],
) -> list[BackchainResult]:
    """Post-process backchain results to align step boundaries with tokenized words.

    When a backchain step starts in the middle of a word (as determined by
    the pipeline's tokenization), extends the step leftward to include the
    full word. Then deduplicates adjacent identical steps that result from
    the fix.

    Args:
        results: Parsed and validated backchain results, sorted by line_index.
        token_texts: Token text per dialogue line, indexed such that
                     token_texts[r.line_index] corresponds to result r.
    """
    fixed_results: list[BackchainResult] = []

    for r in results:
        if r.line_index < 0 or r.line_index >= len(token_texts):
            fixed_results.append(r)
            continue

        tokens = token_texts[r.line_index]
        original = r.original
        if not tokens:
            fixed_results.append(r)
            continue

        char_to_token: dict[int, int] = {}
        char_pos = 0
        for token_idx, token_text in enumerate(tokens):
            for _ in token_text:
                char_to_token[char_pos] = token_idx
                char_pos += 1

        fixed_steps: list[BackchainStep] = []
        for step in r.steps:
            text = step.text
            if not original.endswith(text):
                fixed_steps.append(step)
                continue

            start_idx = len(original) - len(text)
            if start_idx not in char_to_token:
                fixed_steps.append(step)
                continue

            token_idx = char_to_token[start_idx]
            token_start = start_idx
            while token_start > 0 and char_to_token.get(token_start - 1) == token_idx:
                token_start -= 1

            if token_start < start_idx:
                new_text = original[token_start:]
                fixed_steps.append(BackchainStep(
                    text=new_text,
                    is_final=step.is_final,
                ))
            else:
                fixed_steps.append(step)

        deduped: list[BackchainStep] = []
        for step in fixed_steps:
            if not deduped or step.text != deduped[-1].text:
                deduped.append(step)
            elif step.is_final and not deduped[-1].is_final:
                deduped[-1] = BackchainStep(
                    text=deduped[-1].text,
                    is_final=True,
                )

        fixed_results.append(BackchainResult(
            line_index=r.line_index,
            original=r.original,
            steps=deduped,
        ))

    return fixed_results


def _build_fix_input(original_input: str, bad_output: str, errors: list[str]) -> str:
    lines = [
        "# Original Input",
        "",
        original_input.strip(),
        "",
        "# Previous Output (with errors)",
        "",
        bad_output.strip(),
        "",
        "# Validation Errors",
        "",
    ]
    for e in errors:
        lines.append(f"- {e}")
    lines.append("")
    return "\n".join(lines)