"""A tolerant tokenizing BibTeX reader.

``parse_bibtex(text)`` returns one :class:`bibauthor.entry.Entry` per entry in
the file, in file order. The whole point of this module is survival: a real
bibliography is hand-edited, concatenated from several sources, and often
broken, and a citation checker that dies on the first stray brace checks
nothing. So the reader never raises on malformed input. It tokenizes, it
recovers, and it records what it could not make sense of in
``Entry.warnings``.

What it understands:

* entries delimited with braces or parentheses, values delimited with braces
  or double quotes, and bare numeric or macro values
* nested braces in values, and braces inside quoted values
* ``@string`` macro definitions, macro use, and ``#`` concatenation
* ``@preamble`` and ``@comment``, both skipped
* ``crossref`` inheritance, resolved after the whole file is read so a
  forward reference works, with a cycle guard
* duplicate citation keys, all kept, each warned about
* comments and stray text between entries, trailing commas, a missing final
  closing brace, CRLF or LF line endings, and a UTF-8 BOM

This file is pure ASCII.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..entry import Entry
from ..latex import decode_latex, is_brace_protected, strip_braces

__all__ = ["parse_bibtex"]

_WS = " \t\r\n"

# Characters allowed in a field name or a macro name.
_NAME_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:+*/"
)

# Fields whose value is an identifier, not prose. LaTeX-decoding these would
# corrupt them: a tilde in a URL is a tilde, not a non-breaking space.
_VERBATIM_FIELDS = frozenset(
    ("url", "doi", "eprint", "archiveprefix", "primaryclass", "isbn", "issn", "howpublished")
)

_VERBATIM_COMMANDS = ("\\url", "\\href", "\\doi", "\\texttt", "\\path", "\\nolinkurl")

_VERBATIM_ESCAPES = (
    ("\\~{}", "~"),
    ("\\~", "~"),
    ("\\%", "%"),
    ("\\_", "_"),
    ("\\&", "&"),
    ("\\#", "#"),
    ("\\$", "$"),
    ("\\{", "{"),
    ("\\}", "}"),
)

_YEAR_RE = re.compile(r"(1[0-9]{3}|2[0-9]{3})")

_VENUE_FIELDS = ("journal", "booktitle", "publisher")


class _Record(object):
    """A raw entry, before field cleaning and Entry construction."""

    __slots__ = ("key", "entry_type", "line", "fields", "order", "warnings")

    def __init__(self, key: str, entry_type: str, line: int) -> None:
        self.key = key
        self.entry_type = entry_type
        self.line = line
        self.fields: Dict[str, str] = {}
        self.order: List[str] = []
        self.warnings: List[str] = []

    def put(self, name: str, value: str) -> None:
        if name not in self.fields:
            self.order.append(name)
        self.fields[name] = value


class _Reader(object):
    """A cursor over the source text, with 1-based line reporting."""

    __slots__ = ("text", "pos", "n")

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.n = len(text)

    def eof(self) -> bool:
        return self.pos >= self.n

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < self.n else ""

    def line_at(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1

    def skip_ws(self) -> None:
        text = self.text
        while self.pos < self.n and text[self.pos] in _WS:
            self.pos += 1


def _normalize_source(text: str) -> str:
    """Strip a BOM and make all line endings LF without changing line counts."""
    if text.startswith("\ufeff"):
        text = text[1:]
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _read_braced(reader: _Reader) -> str:
    """Read a brace-delimited value, returning the content without the braces."""
    text = reader.text
    start = reader.pos
    depth = 0
    i = start
    n = reader.n
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
                reader.pos = i + 1
                return text[start + 1 : i]
        i += 1
    # Unterminated: take the rest of the file rather than lose it.
    reader.pos = n
    return text[start + 1 : n]


def _read_quoted(reader: _Reader) -> str:
    """Read a double-quote-delimited value; braces inside it are respected."""
    text = reader.text
    start = reader.pos
    depth = 0
    i = start + 1
    n = reader.n
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == '"' and depth == 0:
            reader.pos = i + 1
            return text[start + 1 : i]
        i += 1
    reader.pos = n
    return text[start + 1 : n]


def _skip_group(reader: _Reader) -> None:
    """Skip a balanced brace or parenthesis group starting at the cursor."""
    text = reader.text
    opener = reader.peek()
    closer = "}" if opener == "{" else ")"
    depth = 0
    i = reader.pos
    n = reader.n
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                reader.pos = i + 1
                return
        i += 1
    reader.pos = n


def _read_name(reader: _Reader) -> str:
    text = reader.text
    start = reader.pos
    while reader.pos < reader.n and text[reader.pos] in _NAME_CHARS:
        reader.pos += 1
    return text[start : reader.pos]


def _parse_value(reader: _Reader, macros: Dict[str, str]) -> str:
    """Read one field value, following any ``#`` concatenation."""
    parts: List[str] = []
    while True:
        reader.skip_ws()
        ch = reader.peek()
        if ch == "{":
            parts.append(_read_braced(reader))
        elif ch == '"':
            parts.append(_read_quoted(reader))
        elif ch == "" or ch in ",})":
            break
        else:
            token = _read_name(reader)
            if not token:
                # Something unexpected; step over it so we cannot loop.
                reader.pos += 1
                continue
            if token.isdigit():
                parts.append(token)
            else:
                parts.append(macros.get(token.lower(), token))
        reader.skip_ws()
        if reader.peek() == "#":
            reader.pos += 1
            continue
        break
    return "".join(parts)


def _resync(reader: _Reader) -> None:
    """Advance to the next field boundary after a malformed field."""
    text = reader.text
    if reader.pos < reader.n:
        reader.pos += 1
    while reader.pos < reader.n:
        ch = text[reader.pos]
        if ch in ",})@":
            return
        if ch == "{":
            _skip_group(reader)
            continue
        if ch == '"':
            _read_quoted(reader)
            continue
        reader.pos += 1


def _parse_string(reader: _Reader, macros: Dict[str, str]) -> None:
    reader.skip_ws()
    name = _read_name(reader)
    reader.skip_ws()
    if reader.peek() == "=":
        reader.pos += 1
        value = _parse_value(reader, macros)
        if name:
            macros[name.lower()] = value
    reader.skip_ws()
    if reader.peek() in "})":
        reader.pos += 1


def _parse_entry_body(
    reader: _Reader, record: _Record, macros: Dict[str, str], closer: str
) -> None:
    """Read fields until the entry closes, another entry starts, or EOF."""
    while True:
        reader.skip_ws()
        if reader.eof():
            record.warnings.append(
                "entry '%s' is missing its closing %s" % (record.key, closer)
            )
            return
        ch = reader.peek()
        if ch == ",":
            reader.pos += 1
            continue
        if ch in "})":
            reader.pos += 1
            return
        if ch == "@":
            record.warnings.append(
                "entry '%s' is missing its closing %s" % (record.key, closer)
            )
            return
        before = reader.pos
        name = _read_name(reader)
        reader.skip_ws()
        if reader.peek() != "=":
            record.warnings.append(
                "skipped unparseable text at line %d" % reader.line_at(before)
            )
            reader.pos = before
            _resync(reader)
            continue
        reader.pos += 1
        value = _parse_value(reader, macros)
        if name:
            record.put(name.lower(), value)


def _read_records(text: str) -> List[_Record]:
    reader = _Reader(text)
    macros: Dict[str, str] = {}
    records: List[_Record] = []
    while True:
        at = text.find("@", reader.pos)
        if at < 0:
            break
        line = reader.line_at(at)
        reader.pos = at + 1
        reader.skip_ws()
        start = reader.pos
        while reader.pos < reader.n and text[reader.pos].isalpha():
            reader.pos += 1
        entry_type = text[start : reader.pos].lower()
        if not entry_type:
            continue
        reader.skip_ws()
        opener = reader.peek()
        if entry_type in ("comment", "preamble"):
            if opener in ("{", "("):
                _skip_group(reader)
            else:
                newline = text.find("\n", reader.pos)
                reader.pos = reader.n if newline < 0 else newline + 1
            continue
        if opener not in ("{", "("):
            # A stray "@" in prose, an email address, and so on.
            continue
        closer = "}" if opener == "{" else ")"
        reader.pos += 1
        if entry_type == "string":
            _parse_string(reader, macros)
            continue
        reader.skip_ws()
        key_start = reader.pos
        while reader.pos < reader.n and text[reader.pos] not in ",})" + _WS:
            reader.pos += 1
        key = text[key_start : reader.pos].strip()
        record = _Record(key, entry_type, line)
        if not key:
            record.warnings.append("entry at line %d has no citation key" % line)
        _parse_entry_body(reader, record, macros, closer)
        records.append(record)
    return records


def _resolve_crossrefs(records: List[_Record]) -> None:
    """Inherit missing fields from crossref parents, forward references included."""
    index: Dict[str, _Record] = {}
    for record in records:
        index.setdefault(record.key.lower(), record)
    for record in records:
        parent_ref = record.fields.get("crossref", "")
        if not parent_ref.strip():
            continue
        seen = {record.key.lower()}
        current = record
        while True:
            ref = strip_braces(current.fields.get("crossref", "")).strip()
            if not ref:
                break
            target = ref.lower()
            if target in seen:
                record.warnings.append(
                    "crossref cycle: '%s' points back to '%s'" % (current.key, ref)
                )
                break
            seen.add(target)
            parent = index.get(target)
            if parent is None:
                record.warnings.append("crossref target '%s' not found" % ref)
                break
            for name in parent.order:
                if name == "crossref" or name in record.fields:
                    continue
                record.put(name, parent.fields[name])
            current = parent


def _warn_duplicates(records: List[_Record]) -> None:
    groups: Dict[str, List[_Record]] = {}
    for record in records:
        groups.setdefault(record.key, []).append(record)
    for key, group in groups.items():
        if len(group) < 2 or not key:
            continue
        for record in group:
            others = [str(other.line) for other in group if other is not record]
            record.warnings.append(
                "duplicate citation key '%s' also at line %s" % (key, ", ".join(others))
            )


def _clean(value: str) -> str:
    """Decode LaTeX, drop protective braces, squeeze whitespace."""
    return " ".join(strip_braces(decode_latex(value)).split())


def _clean_verbatim(value: str) -> str:
    """Clean an identifier field without running it through the LaTeX decoder."""
    text = value
    for command in _VERBATIM_COMMANDS:
        text = text.replace(command, "")
    text = strip_braces(text)
    for source, target in _VERBATIM_ESCAPES:
        text = text.replace(source, target)
    return " ".join(text.split())


def _clean_field(name: str, value: str) -> str:
    if name in _VERBATIM_FIELDS:
        return _clean_verbatim(value)
    return _clean(value)


def _split_top_level(value: str, separator: str) -> List[str]:
    """Split on a single-character separator at brace depth zero."""
    pieces: List[str] = []
    buffer: List[str] = []
    depth = 0
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n:
            buffer.append(value[i : i + 2])
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == separator and depth == 0:
            pieces.append("".join(buffer))
            buffer = []
            i += 1
            continue
        buffer.append(ch)
        i += 1
    pieces.append("".join(buffer))
    return pieces


def _split_and(value: str) -> List[str]:
    """Split an author or editor field on the top-level ``and`` separator.

    Only whitespace-delimited ``and`` at brace depth zero separates names, so a
    braced corporate name that happens to contain the word stays whole.
    """
    pieces: List[str] = []
    buffer: List[str] = []
    depth = 0
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n:
            buffer.append(value[i : i + 2])
            i += 2
            continue
        if ch == "{":
            depth += 1
            buffer.append(ch)
            i += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            buffer.append(ch)
            i += 1
            continue
        if depth == 0 and ch in _WS:
            j = i
            while j < n and value[j] in _WS:
                j += 1
            if value[j : j + 3].lower() == "and":
                after = j + 3
                if after >= n or value[after] in _WS:
                    while after < n and value[after] in _WS:
                        after += 1
                    pieces.append("".join(buffer))
                    buffer = []
                    i = after
                    continue
            if buffer:
                buffer.append(" ")
            i = j
            continue
        buffer.append(ch)
        i += 1
    pieces.append("".join(buffer))
    return pieces


def _split_names(value: str) -> List[str]:
    """Split a name list, accepting ``;`` only when there is no top-level ``and``."""
    pieces = _split_and(value)
    if len(pieces) == 1:
        by_semicolon = _split_top_level(value, ";")
        if len(by_semicolon) > 1:
            pieces = by_semicolon
    return [piece for piece in (p.strip() for p in pieces) if piece]


def _parse_year(
    year_field: Optional[str], date_field: Optional[str]
) -> Tuple[Optional[int], Optional[str]]:
    """Read a 4-digit year out of ``year``, falling back to ``date``."""
    for source in (year_field, date_field):
        if source is None:
            continue
        text = _clean(source)
        found = _YEAR_RE.search(text)
        if found:
            return int(found.group(1)), None
    if year_field is not None or date_field is not None:
        shown = _clean(year_field if year_field is not None else date_field)
        return None, "no 4-digit year in '%s'" % shown
    return None, "no year field"


def _build_entry(record: _Record, source_file: str) -> Entry:
    raw: Dict[str, str] = {}
    for name in record.order:
        raw[name] = _clean_field(name, record.fields[name])

    warnings = list(record.warnings)

    authors: List[str] = []
    braced_indices: List[int] = []
    if "author" in record.fields and record.fields["author"].strip():
        for piece in _split_names(record.fields["author"]):
            protected = is_brace_protected(piece)
            name = _clean(piece)
            if not name:
                continue
            if protected:
                braced_indices.append(len(authors))
            authors.append(name)
    elif "editor" in record.fields and record.fields["editor"].strip():
        warnings.append(
            "no author field; this entry is cited by its editor '%s'"
            % raw.get("editor", "")
        )
    else:
        warnings.append("no author field")

    if braced_indices:
        raw["_braced_author_indices"] = ",".join(str(i) for i in braced_indices)

    year, year_warning = _parse_year(
        record.fields.get("year"), record.fields.get("date")
    )
    if year_warning:
        warnings.append(year_warning)

    venue = ""
    for field in _VENUE_FIELDS:
        if raw.get(field):
            venue = raw[field]
            break

    return Entry(
        key=record.key,
        entry_type=record.entry_type,
        title=raw.get("title", ""),
        authors=authors,
        year=year,
        doi=raw.get("doi"),
        venue=venue,
        url=raw.get("url", ""),
        source_file=source_file,
        source_line=record.line,
        raw=raw,
        warnings=warnings,
    )


def parse_bibtex(text: str, source_file: str = "") -> List[Entry]:
    """Parse BibTeX source into entries, in file order.

    Never raises on malformed input: problems are reported through
    ``Entry.warnings``.
    """
    if not text:
        return []
    records = _read_records(_normalize_source(text))
    _resolve_crossrefs(records)
    _warn_duplicates(records)
    return [_build_entry(record, source_file) for record in records]
