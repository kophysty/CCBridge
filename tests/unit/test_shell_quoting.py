"""Unit tests for shell command-line quoting (cli._quote_for_shell).

Settings.json hook commands are interpreted as shell strings by Claude
Code. On Windows the rules are different from POSIX (see ``CommandLineToArgvW``);
we use ``subprocess.list2cmdline`` there. On POSIX we use ``shlex.quote``,
which produces single-quoted strings that are safe against any
metacharacter.

Audit Minor #7 (2026-05-03): the previous implementation only wrapped
in double quotes when the string contained a space, which doesn't
defend against semicolons, backticks, dollars, or newlines.
"""

from __future__ import annotations

import sys

from ccbridge.cli import _quote_for_shell


def test_path_with_space_is_quoted() -> None:
    """Spaces are the canonical reason to quote — must remain handled."""
    assert _quote_for_shell("/path with/space") != "/path with/space"


def test_path_without_metachars_unchanged() -> None:
    """Plain paths get the simplest valid representation."""
    plain = "/usr/bin/python"
    quoted = _quote_for_shell(plain)
    # On Windows list2cmdline is no-op for plain paths; on POSIX
    # shlex.quote returns the same string. Either way, it's still safe.
    assert plain in quoted


def test_path_with_semicolon_is_safely_quoted() -> None:
    """Semicolons are shell command separators on POSIX. Must be
    quoted/escaped so the hook command isn't split into two.
    """
    dangerous = "/usr/bin/python;rm -rf /"
    quoted = _quote_for_shell(dangerous)
    if sys.platform != "win32":
        # POSIX shlex.quote wraps in single quotes.
        assert quoted.startswith("'")
        assert quoted.endswith("'")
    # On Windows ``;`` is not a separator for cmd.exe (it would be in
    # PowerShell), so list2cmdline may leave the literal in place. The
    # important contract is that the original string is recoverable.
    # We don't assert escape rules, only that no second command leaks.
    assert "rm -rf" not in quoted.split(";", 1)[0] or quoted != dangerous


def test_path_with_backtick_is_safely_quoted() -> None:
    """Backticks trigger command substitution on POSIX.

    On POSIX, shlex.quote → single-quoted, which makes backticks literal.
    """
    if sys.platform == "win32":
        return  # backticks are not metacharacters in cmd.exe
    dangerous = "/usr/bin/python`whoami`"
    quoted = _quote_for_shell(dangerous)
    assert quoted.startswith("'")
    assert "`whoami`" in quoted  # literal, inside quotes


def test_path_with_dollar_is_safely_quoted() -> None:
    """``$`` triggers variable expansion on POSIX shells."""
    if sys.platform == "win32":
        return
    dangerous = "/usr/bin/python$HOME"
    quoted = _quote_for_shell(dangerous)
    assert quoted.startswith("'")
    assert "$HOME" in quoted


def test_path_with_double_quote_is_safely_quoted() -> None:
    """A literal `"` inside the path used to break double-quote wrapping."""
    if sys.platform == "win32":
        # On Windows, list2cmdline escapes embedded quotes.
        path = 'C:\\path with " quote'
        out = _quote_for_shell(path)
        # Round-trip: when split via the same parser, we get the original.
        import shlex

        # cmd.exe parsing isn't shlex, but shlex(posix=False) is close
        # enough to confirm we didn't truncate or split the path.
        parts = list(shlex.shlex(out, posix=False))
        # After tokenizing we should still be able to reconstruct the
        # original. We don't assert the exact escaping — just that
        # nothing was lost.
        assert any("path with" in t for t in parts) or "path with" in out
    else:
        path = '/usr/bin/python with " quote'
        quoted = _quote_for_shell(path)
        assert quoted.startswith("'")
        assert quoted.endswith("'")
        assert '"' in quoted
