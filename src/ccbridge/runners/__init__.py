"""Subprocess wrappers around external CLIs (claude, codex)."""

from __future__ import annotations

import shutil
from pathlib import Path


def resolve_executable(name: str) -> str:
    """Resolve ``name`` to a path that ``subprocess.run`` will accept.

    Why this exists: on Windows, ``subprocess.run([name, ...])`` without
    ``shell=True`` calls ``CreateProcess`` directly, which does NOT apply
    PATHEXT. So if ``codex`` is installed only as ``codex.cmd`` (the
    common case for npm/winget packaged CLIs) — ``where.exe codex`` finds
    it, but ``subprocess.run(["codex", ...])`` raises FileNotFoundError.

    We resolve via :func:`shutil.which`, which DOES apply PATHEXT on
    Windows and walks ``PATH`` on POSIX. The returned path is absolute
    and includes the extension (e.g. ``C:\\Tools\\codex.cmd``).

    If ``name`` already contains a path separator (``/`` or ``\\``) we
    trust it and return as-is — the caller is asking for a specific
    file location, not PATH lookup. NB: we use "contains separator"
    rather than ``Path.is_absolute()`` because the latter is platform-
    dependent (``/opt/codex`` is not absolute on Windows but is clearly
    not a bare command name either).

    Raises
    ------
    FileNotFoundError
        Only when ``name`` is a bare command (no path separator) and
        :func:`shutil.which` returned ``None``.
    """
    if "/" in name or "\\" in name:
        return name
    resolved = shutil.which(name)
    if resolved is None:
        raise FileNotFoundError(
            f"executable {name!r} not found in PATH "
            f"(checked PATHEXT on Windows)"
        )
    return resolved


# `Path` import kept available because future helpers may need it.
_ = Path


__all__ = ("resolve_executable",)

