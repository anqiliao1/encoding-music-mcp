import re
from pathlib import Path

import verovio
from mcp.types import TextContent
from fastmcp.tools.tool import ToolResult

from .helpers import get_mei_filepath

# Resolve the Verovio resource path from the installed package.
# The verovio __init__.py sets this via importlib.resources, but that can
# fail in some process contexts (e.g. MCP server launched by Claude Desktop).
_VEROVIO_RESOURCE_PATH = str(Path(verovio.__file__).parent / "data")

__all__ = ["play_excerpt"]

def _inject_or_replace_tempo(mei_text: str, bpm: int = 60) -> str:
    """Ensure the MEI contains the requested playback tempo.

    If the MEI already has a ``midi.bpm`` attribute, replace its first
    occurrence with the supplied value. Otherwise, insert a ``<tempo>``
    element immediately after the opening tag of the first measure.

    Args:
        mei_text: Raw MEI XML as a string.
        bpm: Tempo in beats per minute (default: 60).

    Returns:
        The modified MEI XML string with the requested tempo applied.
    """
    if 'midi.bpm="' in mei_text:
        return re.sub(
            r'midi\.bpm="\d+(\.\d+)?"',
            f'midi.bpm="{bpm}"',
            mei_text,
            count=1,
        )

    return re.sub(
        r'(<measure\b[^>]*>)',
        rf'\1\n  <tempo midi.bpm="{bpm}">♩ = {bpm}</tempo>',
        mei_text,
        count=1,
    )


def _create_toolkit(mei_data: str) -> verovio.toolkit:
    """Create and initialise a Verovio toolkit from MEI data.

    Args:
        mei_data: MEI XML content to load into Verovio.

    Returns:
        An initialised Verovio toolkit containing the loaded MEI.

    Raises:
        ValueError: If Verovio fails to load the MEI data.
    """
    tk = verovio.toolkit()
    tk.setResourcePath(_VEROVIO_RESOURCE_PATH)

    if not tk.loadData(mei_data):
        raise ValueError(
            f"Verovio failed to load MEI data "
            f"(data length={len(mei_data)}, resource_path={tk.getResourcePath()})"
        )
    return tk


def play_excerpt(
    filename: str, start_q: float, end_q: float, bpm: int = 120,
) -> ToolResult:
    """Prepare a playable excerpt from an MEI file.

    Args:
        filename: Name of the MEI file.
        start_q: Start beat in quarter-note units.
        end_q: End beat in quarter-note units.
        bpm: Playback tempo.

    Returns:
        ToolResult with MIDI data and excerpt timing for an MCP app.
    """
    if end_q <= start_q:
        raise ValueError("end_q must be greater than start_q")

    filepath = get_mei_filepath(filename)
    if not filepath.exists():
        raise FileNotFoundError(f"MEI file not found: {filename}")

    mei_text = filepath.read_text(encoding="utf-8")
    mei_text = _inject_or_replace_tempo(mei_text, bpm)

    tk = _create_toolkit(mei_text)
    midi_b64 = tk.renderToMIDI()

    start_sec = start_q * 60 / bpm
    end_sec = end_q * 60 / bpm

    description = (
        f"Prepared excerpt from {filename}, beats {start_q} to {end_q} at {bpm} bpm"
    )

    structured = {
        "filename": filename,
        "midi_base64": midi_b64,
        "start_q": start_q,
        "end_q": end_q,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "bpm": bpm,
    }

    return ToolResult(
        content=[TextContent(type="text", text=description)],
        structured_content=structured,
    )