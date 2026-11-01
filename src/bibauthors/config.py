"""Configuration: thresholds, provider selection, cache location.

Config can come from a TOML file (``[tool.bibauthors]`` in ``pyproject.toml``,
or any ``.toml`` given with ``--config``), from a plain dict, or from CLI
flags. CLI flags win over the file, and the file wins over the defaults.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any, Dict, List, Optional, Sequence

from .providers import PROVIDER_ORDER
from .verdict import Thresholds

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore
    except ImportError:  # pragma: no cover
        _toml = None  # type: ignore

DEFAULT_CACHE_DIRNAME = ".bibauthors-cache"


def default_cache_dir() -> str:
    """Where the cache goes when nobody says otherwise.

    Prefers an explicit ``BIBAUTHORS_CACHE`` environment variable, then an
    XDG-style user cache directory, then a directory beside the bibliography.
    """
    env = os.environ.get("BIBAUTHORS_CACHE")
    if env:
        return env
    base = os.environ.get("XDG_CACHE_HOME") or os.environ.get("LOCALAPPDATA")
    if base:
        return os.path.join(base, "bibauthors")
    home = os.path.expanduser("~")
    if home and home != "~":
        return os.path.join(home, ".cache", "bibauthors")
    return DEFAULT_CACHE_DIRNAME


@dataclasses.dataclass
class Config:
    """Everything the verifier needs that is not the bibliography itself."""

    thresholds: Thresholds = dataclasses.field(default_factory=Thresholds)
    providers: List[str] = dataclasses.field(default_factory=lambda: list(PROVIDER_ORDER))
    cache_dir: str = dataclasses.field(default_factory=default_cache_dir)
    offline: bool = False
    min_interval: float = 1.0
    timeout: float = 20.0
    max_entries: Optional[int] = None
    check_years: bool = True
    stop_after_confident: bool = True
    ignore_keys: List[str] = dataclasses.field(default_factory=list)
    fail_on_unverifiable: bool = False

    def validate(self) -> None:
        self.thresholds.validate()
        unknown = [p for p in self.providers if p not in PROVIDER_ORDER]
        if unknown:
            raise ValueError(
                "unknown provider(s): %s (known: %s)"
                % (", ".join(unknown), ", ".join(PROVIDER_ORDER))
            )
        if not self.providers:
            raise ValueError("at least one provider must be enabled")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """Build a Config from a plain mapping, ignoring unknown keys loudly."""
        data = dict(data or {})
        th_fields = {f.name for f in dataclasses.fields(Thresholds)}
        cfg_fields = {f.name for f in dataclasses.fields(cls)} - {"thresholds"}

        th_data = dict(data.pop("thresholds", {}) or {})
        # Threshold keys are also accepted at the top level, which is what
        # people actually write in a pyproject table.
        for name in list(data):
            if name in th_fields:
                th_data[name] = data.pop(name)

        bad_th = [k for k in th_data if k not in th_fields]
        if bad_th:
            raise ValueError("unknown threshold key(s): %s" % ", ".join(sorted(bad_th)))
        bad_cfg = [k for k in data if k not in cfg_fields]
        if bad_cfg:
            raise ValueError("unknown config key(s): %s" % ", ".join(sorted(bad_cfg)))

        cfg = cls(thresholds=Thresholds(**th_data), **data)
        cfg.validate()
        return cfg

    @classmethod
    def from_toml(cls, path: str) -> "Config":
        """Load ``[tool.bibauthors]`` from a TOML file, or its whole table."""
        if _toml is None:  # pragma: no cover
            raise ValueError(
                "reading a TOML config needs Python 3.11+ or the 'tomli' package"
            )
        with open(path, "rb") as handle:
            doc = _toml.load(handle)
        table = doc.get("tool", {}).get("bibauthors")
        if table is None:
            table = doc.get("bibauthors", doc)
        return cls.from_dict(table)

    @classmethod
    def discover(cls, start: str = ".") -> "Config":
        """Look for a config beside the bibliography, then upwards.

        Checks ``bibauthors.toml`` then ``pyproject.toml`` in each directory
        from ``start`` up to the filesystem root. Returns defaults if none has
        a bibauthors table.
        """
        here = os.path.abspath(start)
        if os.path.isfile(here):
            here = os.path.dirname(here)
        seen = set()
        while here and here not in seen:
            seen.add(here)
            for name in ("bibauthors.toml", "pyproject.toml"):
                candidate = os.path.join(here, name)
                if not os.path.isfile(candidate):
                    continue
                if _toml is None:  # pragma: no cover
                    continue
                with open(candidate, "rb") as handle:
                    doc = _toml.load(handle)
                table = doc.get("tool", {}).get("bibauthors")
                if table is None and name == "bibauthors.toml":
                    table = doc.get("bibauthors")
                if table is not None:
                    return cls.from_dict(table)
            parent = os.path.dirname(here)
            if parent == here:
                break
            here = parent
        return cls()

    def with_overrides(self, **kwargs: Any) -> "Config":
        """Return a copy with non-None overrides applied.

        Threshold names are routed into ``thresholds`` automatically, so the
        CLI can pass everything in one flat call.
        """
        th_fields = {f.name for f in dataclasses.fields(Thresholds)}
        th = dataclasses.replace(self.thresholds)
        cfg_updates: Dict[str, Any] = {}
        for key, value in kwargs.items():
            if value is None:
                continue
            if key in th_fields:
                setattr(th, key, value)
            else:
                cfg_updates[key] = value
        out = dataclasses.replace(self, thresholds=th, **cfg_updates)
        out.validate()
        return out


def parse_threshold_overrides(pairs: Sequence[str]) -> Dict[str, Any]:
    """Turn ``["name_variant=0.75", "promote_truncated=true"]`` into a dict."""
    out: Dict[str, Any] = {}
    fields = {f.name: f.type for f in dataclasses.fields(Thresholds)}
    for pair in pairs or ():
        if "=" not in pair:
            raise ValueError("expected NAME=VALUE, got %r" % pair)
        name, _, raw = pair.partition("=")
        name = name.strip()
        raw = raw.strip()
        if name not in fields:
            raise ValueError(
                "unknown threshold %r (known: %s)"
                % (name, ", ".join(sorted(fields)))
            )
        declared = str(fields[name])
        if "bool" in declared:
            lowered = raw.lower()
            if lowered not in ("true", "false", "1", "0", "yes", "no"):
                raise ValueError("%s wants a boolean, got %r" % (name, raw))
            out[name] = lowered in ("true", "1", "yes")
        elif "int" in declared:
            out[name] = int(raw)
        else:
            out[name] = float(raw)
    return out
