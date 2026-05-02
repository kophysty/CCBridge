"""Unit tests for ccbridge.runners.resolve_executable.

This is the helper that fixes the Windows PATHEXT problem: bare
``codex`` in subprocess argv won't find ``codex.cmd`` because
``CreateProcess`` does not apply PATHEXT. ``shutil.which`` does.

Discovered in audit: live ``codex`` install on the user's Windows
machine put ``codex.cmd`` into PATH, ``where.exe codex`` saw it,
but ``run_codex(executable="codex")`` raised FileNotFoundError.
"""

from __future__ import annotations

import shutil

import pytest

from ccbridge.runners import resolve_executable


def test_absolute_path_is_returned_as_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absolute paths are trusted; we don't probe the filesystem.

    This lets tests pass synthetic paths to subprocess stubs without
    needing the file to exist.
    """
    called = {"flag": False}

    def fake_which(name: str) -> str | None:
        called["flag"] = True
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    assert resolve_executable("/opt/codex/bin/codex") == "/opt/codex/bin/codex"
    assert resolve_executable("C:/Tools/codex.cmd") == "C:/Tools/codex.cmd"
    assert called["flag"] is False


def test_bare_name_resolves_via_shutil_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    assert resolve_executable("codex") == "/usr/local/bin/codex"


def test_bare_name_resolves_to_cmd_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact bug we're fixing: shutil.which on Windows returns the
    .cmd extension. Our helper passes that through to subprocess.run.
    """
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "C:\\Users\\u\\AppData\\Roaming\\npm\\codex.cmd"
        if name == "codex"
        else None,
    )
    resolved = resolve_executable("codex")
    assert resolved.endswith("codex.cmd")


def test_bare_name_not_in_path_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError) as exc_info:
        resolve_executable("nonexistent_binary_xyz")
    assert "not found" in str(exc_info.value).lower()


def test_path_with_separator_passes_through_without_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anything that contains a ``/`` or ``\\`` is treated as a path,
    not a bare command name — even relative paths like ``./tools/codex``.

    We deliberately do NOT call shutil.which on these; the helper's
    contract is "if the caller is asking for a specific file location,
    don't probe PATH". This matches the abs-path passthrough semantics
    and keeps behaviour platform-independent (``Path.is_absolute()``
    differs between POSIX and Windows).
    """
    called = {"flag": False}

    def fake_which(name: str) -> str | None:
        called["flag"] = True
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    assert resolve_executable("./tools/codex") == "./tools/codex"
    assert resolve_executable("tools\\codex.cmd") == "tools\\codex.cmd"
    assert called["flag"] is False
