"""LaTeX source to Unicode text, and BibTeX protective-brace handling.

Two public helpers:

``decode_latex(text)``
    Turn LaTeX source into Unicode text. Total: it never raises, and it never
    throws away the text surrounding a construct it does not understand.

``strip_braces(text)``
    Remove BibTeX protective braces while keeping the inner text.

Because a fully brace-protected field such as ``{The LIGO Scientific
Collaboration}`` is the conventional signal for a group (consortium) author,
that signal has to survive brace stripping. Two extra helpers expose it:
``is_brace_protected(text)`` and ``strip_braces_report(text)``.

This file is pure ASCII: every non-ASCII character is written as a Python
``\\u`` escape.
"""

from __future__ import annotations

import unicodedata
from typing import List, Tuple

__all__ = [
    "decode_latex",
    "strip_braces",
    "strip_braces_report",
    "is_brace_protected",
]

# Accent commands, mapped to the combining mark that follows the base letter.
# Both the one-character spellings and the braced ones end up here; the
# difference is only in how the argument is read.
_ACCENTS = {
    "'": "\u0301",  # acute
    "`": "\u0300",  # grave
    "^": "\u0302",  # circumflex
    '"': "\u0308",  # diaeresis
    "~": "\u0303",  # tilde
    "=": "\u0304",  # macron
    ".": "\u0307",  # dot above
    "u": "\u0306",  # breve
    "v": "\u030c",  # caron
    "H": "\u030b",  # double acute
    "c": "\u0327",  # cedilla
    "d": "\u0323",  # dot below
    "b": "\u0331",  # macron below
    "r": "\u030a",  # ring above
    "k": "\u0328",  # ogonek
    "t": "\u0361",  # tie, sits between two letters
}

# What an accent command with an empty argument produces on its own.
_ACCENT_ALONE = {
    "'": "\u00b4",
    "`": "`",
    "^": "^",
    '"': "\u00a8",
    "~": "~",
    "=": "\u00af",
    ".": ".",
}

# Letters that LaTeX spells as a command.
_SPECIALS = {
    "ss": "\u00df",
    "o": "\u00f8",
    "O": "\u00d8",
    "l": "\u0142",
    "L": "\u0141",
    "aa": "\u00e5",
    "AA": "\u00c5",
    "ae": "\u00e6",
    "AE": "\u00c6",
    "oe": "\u0153",
    "OE": "\u0152",
    "dh": "\u00f0",
    "DH": "\u00d0",
    "th": "\u00fe",
    "TH": "\u00de",
    "i": "\u0131",
    "j": "\u0237",
    "dj": "\u0111",
    "DJ": "\u0110",
}

# Dotless i and j only exist so that an accent can be put on them; inside an
# accent argument they stand for the plain letter.
_DOTLESS = {"\u0131": "i", "\u0237": "j"}

# Single-character escapes and spacing commands.
_ESCAPES = {
    "&": "&",
    "%": "%",
    "$": "$",
    "#": "#",
    "_": "_",
    "{": "{",
    "}": "}",
    " ": " ",
    "\t": " ",
    "\\": " ",
    ",": " ",
    ";": " ",
    ":": " ",
    "-": "",
    "!": "",
    "/": "",
    "@": "",
}

_WS = " \t\r\n"


def _match_brace(text: str, start: int) -> int:
    """Index of the closing brace matching the one at ``start``, or -1."""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _command_name(text: str, i: int) -> Tuple[str, int]:
    """Read the command starting at the backslash index ``i``.

    Returns ``(name, next_index)``. ``name`` is a run of letters for a control
    word, or the single character for a control symbol. Empty when the string
    ends right after the backslash.
    """
    n = len(text)
    j = i + 1
    if j >= n:
        return "", j
    if text[j].isalpha():
        k = j
        while k < n and text[k].isalpha():
            k += 1
        return text[j:k], k
    return text[j], j + 1


def _should_unwrap(inner: str) -> bool:
    """True for a brace group that only protects one accent or special letter.

    A braced accent is a spelling of a single character, so its braces are not
    protective and must go. A group author name and a braced unknown command
    keep theirs, so that the brace-protection signal and the capitalisation
    protection both survive.
    """
    stripped = inner.strip()
    if not stripped.startswith("\\"):
        return False
    name, _ = _command_name(stripped, 0)
    if not name:
        return False
    if len(name) == 1 and not name.isalpha():
        return name in _ACCENTS or name in _ESCAPES
    return name in _ACCENTS or name in _SPECIALS


def _skip_optional_space(text: str, i: int) -> int:
    """Consume the whitespace a control word absorbs, plus an empty group."""
    n = len(text)
    if text[i : i + 2] == "{}":
        i += 2
    while i < n and text[i] in _WS:
        i += 1
    return i


def _read_argument(text: str, i: int) -> Tuple[str, int]:
    """Read an accent or command argument starting at ``i``.

    Returns the decoded argument text and the index just past it.
    """
    n = len(text)
    if i >= n:
        return "", i
    ch = text[i]
    if ch == "{":
        end = _match_brace(text, i)
        if end < 0:
            return _decode(text[i + 1 :]), n
        return _decode(text[i + 1 : end]), end + 1
    if ch == "\\":
        name, after = _command_name(text, i)
        if name in _SPECIALS:
            return _SPECIALS[name], _skip_optional_space(text, after)
        if name in _ACCENTS:
            return _apply_accent(text, name, after)
        if len(name) == 1 and name in _ESCAPES:
            return _ESCAPES[name], after
        if not name:
            return "", after
        # Unknown command inside an argument: drop it, keep its own argument.
        if name.isalpha():
            after = _skip_optional_space(text, after)
        return _read_argument(text, after)
    return ch, i + 1


def _apply_accent(text: str, name: str, after: int) -> Tuple[str, int]:
    """Apply the accent command ``name`` to whatever follows index ``after``."""
    mark = _ACCENTS[name]
    pos = after
    if name.isalpha():
        pos = _skip_optional_space(text, pos)
    base, pos = _read_argument(text, pos)
    base = _DOTLESS.get(base, base)
    if not base:
        return _ACCENT_ALONE.get(name, ""), pos
    return base[0] + mark + base[1:], pos


def _decode(text: str) -> str:
    """The scanner proper. Takes LaTeX source, returns plain text."""
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            name, after = _command_name(text, i)
            if not name:
                i = after
                continue
            if name in _SPECIALS:
                out.append(_SPECIALS[name])
                i = _skip_optional_space(text, after)
                continue
            if name in _ACCENTS:
                piece, i = _apply_accent(text, name, after)
                out.append(piece)
                continue
            if len(name) == 1 and name in _ESCAPES:
                out.append(_ESCAPES[name])
                i = after
                continue
            if len(name) == 1:
                # Unknown control symbol: drop it.
                i = after
                continue
            # Unknown control word: drop the command, keep a braced argument.
            j = after
            while j < n and text[j] in _WS:
                j += 1
            if j < n and text[j] == "{":
                end = _match_brace(text, j)
                if end < 0:
                    out.append(_decode(text[j + 1 :]))
                    i = n
                else:
                    out.append(_decode(text[j + 1 : end]))
                    i = end + 1
                continue
            i = _skip_optional_space(text, after)
            continue
        if ch == "{":
            end = _match_brace(text, i)
            if end < 0:
                out.append("{")
                i += 1
                continue
            inner = text[i + 1 : end]
            decoded = _decode(inner)
            if _should_unwrap(inner):
                out.append(decoded)
            else:
                out.append("{" + decoded + "}")
            i = end + 1
            continue
        if ch == "$":
            # Math mode: strip the delimiters, leave the content alone.
            if text.startswith("$$", i):
                end = text.find("$$", i + 2)
                if end >= 0:
                    out.append(text[i + 2 : end])
                    i = end + 2
                    continue
            else:
                end = text.find("$", i + 1)
                if end >= 0:
                    out.append(text[i + 1 : end])
                    i = end + 1
                    continue
            out.append("$")
            i += 1
            continue
        if ch == "~":
            out.append("\u00a0")
            i += 1
            continue
        if ch == "-":
            if text.startswith("---", i):
                out.append("\u2014")
                i += 3
            elif text.startswith("--", i):
                out.append("\u2013")
                i += 2
            else:
                out.append("-")
                i += 1
            continue
        if text.startswith("``", i):
            out.append("\u201c")
            i += 2
            continue
        if text.startswith("''", i):
            out.append("\u201d")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def decode_latex(text: str) -> str:
    """Decode LaTeX source to Unicode text.

    Never raises. Anything the decoder does not recognise is dropped, command
    and all, but its braced argument and all surrounding text are kept.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    if not text:
        return ""
    try:
        decoded = _decode(text)
    except Exception:
        return text
    try:
        return unicodedata.normalize("NFC", decoded)
    except Exception:
        return decoded


def is_brace_protected(text: str) -> bool:
    """True when the whole field is wrapped in one matching brace pair.

    A group author name in braces is protected; a field holding two separately
    braced names is not, because the first opening brace does not match the
    last closing one.
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 2 or not stripped.startswith("{") or not stripped.endswith("}"):
        return False
    end = _match_brace(stripped, 0)
    return end == len(stripped) - 1


def strip_braces(text: str) -> str:
    """Remove BibTeX braces, keeping the text they protect.

    Escaped braces are kept as literal braces. Unbalanced braces are simply
    dropped rather than treated as an error.
    """
    if not text:
        return ""
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            out.append(text[i : i + 2])
            i += 2
            continue
        if ch in "{}":
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_braces_report(text: str) -> Tuple[str, bool]:
    """``strip_braces`` plus the brace-protection flag, in one call."""
    return strip_braces(text), is_brace_protected(text)
