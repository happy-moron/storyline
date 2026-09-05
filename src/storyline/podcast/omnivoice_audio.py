import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from pydub import AudioSegment

from storyline.logging import get_logger

_log = get_logger("audio.omnivoice")

_VENV_DIR = Path("/mnt/mac/git/omnivoice/.venv")
_OMNIVOICE_BIN = _VENV_DIR / "bin" / "omnivoice-infer"
_OMNIVOICE_BATCH_BIN = _VENV_DIR / "bin" / "omnivoice-infer-batch"
_DEFAULT_MODEL = "k2-fsa/OmniVoice"
_DEFAULT_BATCH_SIZE = 6


class OmnivoiceTTSService:
    """CUDA/GPU voice cloning via omnivoice-infer subprocess.

    Only supports voice cloning (text + ref_audio + ref_text).
    Voice design must use Qwen3TTSService instead.
    """

    def __init__(
        self,
        *,
        binary: str | Path = _OMNIVOICE_BIN,
        model: str = _DEFAULT_MODEL,
        timeout: int = 1200,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        batch_binary: str | Path | None = None,
        batch_model: str | None = None,
        **__kwargs,
    ):
        self.binary = str(binary)
        self.batch_binary = str(batch_binary) if batch_binary else str(_OMNIVOICE_BATCH_BIN)
        self.model = model
        self.batch_model = batch_model if batch_model is not None else _DEFAULT_MODEL
        self.timeout = timeout
        self.batch_size = batch_size

    def generate_voice_clone(
        self, text: str, language: str, ref_audio_path: str, ref_text: str,
    ) -> AudioSegment:
        del language
        cmd = [
            self.binary, "--model", self.model,
            "--text", text,
            "--ref_audio", ref_audio_path,
            "--ref_text", ref_text,
        ]
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        cmd += ["--output", tmp_path]
        _log.info("event=omnivoice_call label=voice_clone")
        _log.debug("event=omnivoice_cmd cmd=%s", " ".join(cmd))
        try:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"Omnivoice voice_clone timed out after {self.timeout}s")

            if result.returncode != 0:
                raise RuntimeError(
                    f"Omnivoice voice_clone failed (rc={result.returncode}): "
                    f"stderr={result.stderr.strip()}"
                )

            output_path = Path(tmp_path)
            if not output_path.exists():
                raise RuntimeError(
                    f"Omnivoice voice_clone completed but output file not found: {output_path}"
                )
            return AudioSegment.from_wav(str(output_path))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def generate_voice_clone_batch(
        self,
        texts: Sequence[str],
        languages: Sequence[str] | str,
        ref_audio_path: str,
        ref_text: str,
    ) -> list[AudioSegment]:
        if isinstance(languages, str):
            languages = [languages] * len(texts)

        results: list[AudioSegment] = []

        for chunk_start in range(0, len(texts), self.batch_size):
            chunk_end = min(chunk_start + self.batch_size, len(texts))
            chunk_texts = texts[chunk_start:chunk_end]

            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)

                entries = []
                for j, text in enumerate(chunk_texts):
                    entries.append({
                        "id": f"{chunk_start + j:06d}",
                        "text": text,
                        "ref_audio": ref_audio_path,
                        "ref_text": ref_text,
                    })

                test_list_path = tmp_dir / "batch.jsonl"
                with test_list_path.open("w", encoding="utf-8") as f:
                    for entry in entries:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

                res_dir = tmp_dir / "results"
                res_dir.mkdir()

                cmd = [
                    self.batch_binary,
                    "--model", self.batch_model,
                    "--test_list", str(test_list_path),
                    "--res_dir", str(res_dir),
                ]

                env = os.environ.copy()
                env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

                _log.info(
                    "event=omnivoice_batch_call offset=%d size=%d",
                    chunk_start, len(chunk_texts),
                )
                _log.debug("event=omnivoice_batch_cmd cmd=%s", " ".join(cmd))

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                    start_new_session=True,
                )

                try:
                    stdout, stderr = proc.communicate(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    self._kill_process_group(proc)
                    self._kill_zombie_omnivoice()
                    raise RuntimeError(
                        f"Omnivoice batch timed out after {self.timeout}s "
                        f"(offset={chunk_start})"
                    )

                if proc.returncode != 0:
                    raise RuntimeError(
                        f"Omnivoice batch failed (rc={proc.returncode}): "
                        f"stderr={stderr.strip()}"
                    )

                stderr_tail = stderr.strip()[-2000:] if stderr else "(no stderr)"
                stdout_tail = stdout.strip()[-2000:] if stdout else "(no stdout)"

                missing = []
                for j in range(len(chunk_texts)):
                    wav_path = res_dir / f"{chunk_start + j:06d}.wav"
                    if not wav_path.exists():
                        missing.append(str(wav_path))
                    else:
                        results.append(AudioSegment.from_wav(str(wav_path)))

                if missing:
                    _log.error("event=omnivoice_batch_missing count=%d files=%s",
                               len(missing), missing[:5])
                    _log.error("event=omnivoice_batch_stderr stderr=%s", stderr_tail)
                    _log.error("event=omnivoice_batch_stdout stdout=%s", stdout_tail)
                    self._kill_zombie_omnivoice()
                    raise RuntimeError(
                        f"Omnivoice batch: {len(missing)}/{len(chunk_texts)} files missing. "
                        f"First: {missing[0]}. stderr (last 2000 chars): {stderr_tail}"
                    )

        return results

    @staticmethod
    def _kill_process_group(proc: subprocess.Popen):
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=10)
            _log.warning("event=omnivoice_pg_killed pgid=%d pid=%d", pgid, proc.pid)
        except (ProcessLookupError, OSError):
            pass

    def _kill_zombie_omnivoice(self):
        try:
            result = subprocess.run(
                ["pgrep", "-f", r"omnivoice.*(infer|batch)"],
                capture_output=True, text=True, timeout=5,
            )
            pids = [int(pid) for pid in result.stdout.strip().split("\n") if pid]
            for pid in pids:
                if pid == os.getpid():
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                    _log.warning("event=omnivoice_zombie_killed pid=%d", pid)
                except OSError:
                    pass
        except Exception:
            pass