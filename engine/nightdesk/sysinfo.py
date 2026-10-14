"""How much of the Mac a program is using, read from macOS itself."""
from __future__ import annotations

import re
import subprocess

_UNITS = {"KB": 1e-3, "MB": 1.0, "GB": 1e3}


def parse_footprint(text: str) -> float | None:
    """Megabytes, from the first line of the `footprint` tool: 'python [123]: 64-bit    Footprint: 518 MB ...'."""
    m = re.search(r"Footprint:\s*([\d.]+)\s*(KB|MB|GB)", text)
    return round(float(m.group(1)) * _UNITS[m.group(2)], 1) if m else None


def footprint_mb(pid: int) -> float | None:
    """The real memory a running program is holding right now (the figure Activity Monitor calls Memory).

    Asked of macOS's own `footprint` tool, because the usual "resident size" misses memory held on the
    graphics chip, which is where an AI model lives on a Mac. Falls back to resident size elsewhere."""
    try:
        out = subprocess.run(["/usr/bin/footprint", "-p", str(pid)], capture_output=True, text=True, timeout=15).stdout
        got = parse_footprint(out)
        if got is not None:
            return got
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        return round(int(out.strip()) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
