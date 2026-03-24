import base64
import json
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import verovio
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from .helpers import get_mei_filepath

_VEROVIO_RESOURCE_PATH = str(Path(verovio.__file__).parent / "data")
_SOUNDFONT_PATH = Path(__file__).resolve().parent.parent / "resources" / "GeneralUser-GS.sf2"
_FALLBACK_FLUIDSYNTH_EXE = Path(r"C:\ProgramData\chocolatey\bin\fluidsynth.exe")

__all__ = ["play_excerpt"]


def _inject_or_replace_tempo(mei_text: str, bpm: int) -> str:
    if 'midi.bpm="' in mei_text:
        return re.sub(
            r'midi\.bpm="\d+(\.\d+)?"',
            f'midi.bpm="{bpm}"',
            mei_text,
            count=1,
        )

    return re.sub(
        r"(<measure\b[^>]*>)",
        rf'\1\n  <tempo midi.bpm="{bpm}">♩ = {bpm}</tempo>',
        mei_text,
        count=1,
    )


def _create_toolkit(mei_data: str) -> verovio.toolkit:
    tk = verovio.toolkit()
    tk.setResourcePath(_VEROVIO_RESOURCE_PATH)
    if not tk.loadData(mei_data):
        raise ValueError("Verovio failed to load the MEI data.")
    return tk


def _find_fluidsynth_executable() -> Path:
    exe = shutil.which("fluidsynth")
    if exe:
        return Path(exe)

    if _FALLBACK_FLUIDSYNTH_EXE.exists():
        return _FALLBACK_FLUIDSYNTH_EXE

    raise FileNotFoundError(
        "FluidSynth executable not found. "
        "Install it on Windows and make sure it is on PATH, "
        f"or place it at {_FALLBACK_FLUIDSYNTH_EXE}."
    )


def _render_midi_b64_to_wav_file(midi_b64: str, wav_path: Path) -> None:
    if not _SOUNDFONT_PATH.exists():
        raise FileNotFoundError(
            f"SoundFont not found at {_SOUNDFONT_PATH}. "
            "Put GeneralUser-GS.sf2 there or update _SOUNDFONT_PATH."
        )

    fluidsynth_exe = _find_fluidsynth_executable()
    midi_bytes = base64.b64decode(midi_b64)

    with tempfile.TemporaryDirectory() as tmpdir:
        midi_path = Path(tmpdir) / "full.mid"
        midi_path.write_bytes(midi_bytes)

        cmd = [
            str(fluidsynth_exe),
            "-q",
            "-ni",
            "-o", "audio.driver=file",
            "-T", "wav",
            "-F", str(wav_path),
            "-r", "44100",
            str(_SOUNDFONT_PATH),
            str(midi_path),
        ]

        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                f"FluidSynth failed with exit code {result.returncode}"
                + (f". stderr: {stderr}" if stderr else "")
            )

        if not wav_path.exists():
            raise RuntimeError("FluidSynth did not produce a WAV file.")


def _trim_wav_file(input_wav: Path, output_wav: Path, start_sec: float, end_sec: float) -> None:
    if end_sec <= start_sec:
        raise ValueError("end_sec must be greater than start_sec")

    with wave.open(str(input_wav), "rb") as src:
        nchannels = src.getnchannels()
        sampwidth = src.getsampwidth()
        framerate = src.getframerate()
        comptype = src.getcomptype()
        compname = src.getcompname()
        nframes = src.getnframes()

        duration_sec = nframes / framerate
        start_sec = max(0.0, min(start_sec, duration_sec))
        end_sec = max(0.0, min(end_sec, duration_sec))

        if end_sec <= start_sec:
            raise ValueError(
                f"Requested excerpt is empty after clamping. "
                f"Audio duration is {duration_sec:.3f}s, "
                f"requested [{start_sec:.3f}, {end_sec:.3f}]s."
            )

        start_frame = int(start_sec * framerate)
        end_frame = int(end_sec * framerate)
        frame_count = max(0, end_frame - start_frame)

        src.setpos(start_frame)
        audio_frames = src.readframes(frame_count)

    with wave.open(str(output_wav), "wb") as dst:
        dst.setparams((nchannels, sampwidth, framerate, 0, comptype, compname))
        dst.writeframes(audio_frames)


def _wav_file_to_b64(wav_path: Path) -> str:
    return base64.b64encode(wav_path.read_bytes()).decode("ascii")


def play_excerpt(
    filename: str,
    start_q: float,
    end_q: float,
    bpm: int = 60,
) -> ToolResult:
    if end_q <= start_q:
        raise ValueError("end_q must be greater than start_q")
    if bpm <= 0:
        raise ValueError("bpm must be positive")

    filepath = get_mei_filepath(filename)
    if not filepath.exists():
        raise FileNotFoundError(f"MEI file not found: {filename}")

    mei_text = filepath.read_text(encoding="utf-8")
    mei_text = _inject_or_replace_tempo(mei_text, bpm)

    tk = _create_toolkit(mei_text)
    midi_b64 = tk.renderToMIDI()

    start_sec = start_q * 60.0 / bpm
    end_sec = (end_q+0.25) * 60.0 / bpm

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        full_wav_path = tmpdir_path / "full.wav"
        excerpt_wav_path = tmpdir_path / "excerpt.wav"

        _render_midi_b64_to_wav_file(midi_b64, full_wav_path)
        _trim_wav_file(full_wav_path, excerpt_wav_path, start_sec, end_sec)
        audio_b64 = _wav_file_to_b64(excerpt_wav_path)

    payload = {
        "filename": filename,
        "audio_base64": audio_b64,
        "mime_type": "audio/wav",
        "start_q": start_q,
        "end_q": end_q,
        "bpm": bpm,
    }

    return ToolResult(
        content=[TextContent(type="text", text="Prepared audio excerpt")],
        structured_content=payload,
    )