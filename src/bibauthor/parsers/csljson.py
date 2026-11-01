"""CSL-JSON reader.

``parse_csl_json(text)`` accepts a top-level JSON array of CSL items, or an
object wrapping that array under ``references`` or ``items``. Unlike the
BibTeX reader this one does raise, because malformed JSON is unambiguous: a
file that is not JSON was never a CSL-JSON file in the first place.

This file is pure ASCII.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..entry import Entry

__all__ = ["parse_csl_json"]

_YEAR_RE = re.compile(r"(1[0-9]{3}|2[0-9]{3})")

_WRAPPER_KEYS = ("references", "items", "entries", "bibliography")

# CSL field -> the key used in Entry.raw. The raw map is what Entry.__post_init__
# reads when it goes looking for an arXiv id, so container-title is stored under
# "journal" as well: a CSL preprint often carries its arXiv id there.
_SCALAR_FIELDS = (
    ("id", "id"),
    ("type", "type"),
    ("title", "title"),
    ("container-title", "container-title"),
    ("publisher", "publisher"),
    ("DOI", "doi"),
    ("URL", "url"),
    ("note", "note"),
    ("number", "number"),
    ("volume", "volume"),
    ("page", "pages"),
)


def _as_text(value: Any) -> str:
    """Flatten a CSL scalar to a string without inventing content."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value if item is not None).strip()
    if isinstance(value, dict):
        for key in ("literal", "raw", "name", "value"):
            if key in value:
                return _as_text(value[key])
        return ""
    return str(value)


def _author_display(person: Any) -> str:
    """Build one display string for a CSL name object."""
    if isinstance(person, str):
        return person.strip()
    if not isinstance(person, dict):
        return _as_text(person)
    literal = _as_text(person.get("literal"))
    if literal:
        return literal
    family = _as_text(person.get("family"))
    given = _as_text(person.get("given"))
    suffix = _as_text(person.get("suffix"))
    if family and given:
        display = "%s, %s" % (family, given)
    else:
        display = family or given
    if suffix:
        display = "%s, %s" % (display, suffix) if display else suffix
    if display:
        return display
    return _as_text(person.get("name"))


def _authors_of(item: Dict[str, Any]) -> List[str]:
    people = item.get("author")
    if people is None:
        return []
    if isinstance(people, (str, dict)):
        people = [people]
    if not isinstance(people, list):
        return []
    out: List[str] = []
    for person in people:
        display = _author_display(person)
        if display:
            out.append(display)
    return out


def _issued_year(item: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """Return ``(year, iso_or_raw_text)`` from the CSL ``issued`` field."""
    issued = item.get("issued")
    if issued is None:
        return None, ""
    if isinstance(issued, (str, int)):
        text = _as_text(issued)
        found = _YEAR_RE.search(text)
        return (int(found.group(1)) if found else None), text
    if not isinstance(issued, dict):
        return None, ""
    parts = issued.get("date-parts")
    if isinstance(parts, list) and parts:
        first = parts[0]
        if isinstance(first, list) and first:
            text = "-".join(
                str(piece) for piece in first if piece not in (None, "")
            )
            found = _YEAR_RE.search(str(first[0]))
            if found:
                return int(found.group(1)), text
            return None, text
    for key in ("raw", "literal"):
        text = _as_text(issued.get(key))
        if text:
            found = _YEAR_RE.search(text)
            return (int(found.group(1)) if found else None), text
    return None, ""


def _build_entry(item: Dict[str, Any], index: int, source_file: str) -> Entry:
    raw: Dict[str, str] = {}
    for csl_name, raw_name in _SCALAR_FIELDS:
        value = _as_text(item.get(csl_name))
        if value:
            raw[raw_name] = value

    container = raw.get("container-title", "")
    if container and "journal" not in raw:
        raw["journal"] = container

    warnings: List[str] = []
    key = _as_text(item.get("id"))
    if not key:
        key = "csl-item-%d" % (index + 1)
        warnings.append("CSL item %d has no 'id'; using '%s'" % (index + 1, key))

    authors = _authors_of(item)
    if not authors:
        if item.get("editor"):
            warnings.append("no author field; this entry is cited by its editor")
        else:
            warnings.append("no author field")
    if authors:
        raw["author"] = " and ".join(authors)

    year, issued_text = _issued_year(item)
    if issued_text:
        raw["issued"] = issued_text
    if year is None:
        warnings.append("no 4-digit year in 'issued'")

    entry_type = raw.get("type", "") or "misc"

    venue = container or raw.get("publisher", "")

    return Entry(
        key=key,
        entry_type=entry_type.lower(),
        title=raw.get("title", ""),
        authors=authors,
        year=year,
        doi=raw.get("doi"),
        venue=venue,
        url=raw.get("url", ""),
        source_file=source_file,
        source_line=None,
        raw=raw,
        warnings=warnings,
    )


def parse_csl_json(text: str, source_file: str = "") -> List[Entry]:
    """Parse CSL-JSON into entries.

    Raises ``ValueError`` when the text is not JSON, or is JSON of a shape that
    is not a CSL-JSON item list.
    """
    if text is None or not text.strip():
        return []
    body = text.lstrip("\ufeff")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError("not valid JSON: %s" % exc)

    items: Any = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for wrapper in _WRAPPER_KEYS:
            candidate = data.get(wrapper)
            if isinstance(candidate, list):
                items = candidate
                break
        if items is None and ("id" in data or "title" in data):
            items = [data]
    if items is None:
        raise ValueError(
            "CSL-JSON must be an array of items, or an object with a "
            "'references' or 'items' array"
        )

    entries: List[Entry] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("CSL-JSON item %d is not an object" % (index + 1))
        entries.append(_build_entry(item, index, source_file))
    return entries
