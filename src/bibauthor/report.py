"""Reports: one for people, one for machines.

The JSON report has a stable schema (``schema_version``), because the point of
this tool is that other repositories depend on it. Fields are added, never
renamed or removed, within a major schema version.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Dict, List, Optional, TextIO

from .verdict import DESCRIPTIONS, SEVERITY, Verdict, is_mismatch
from .verify import EntryResult, Report, SCHEMA_VERSION

#: Printed next to each verdict in the human report. ASCII only on purpose:
#: CI logs, Windows consoles and pre-commit output all mangle anything else.
MARKS = {
    Verdict.MATCH: "ok  ",
    Verdict.MATCH_WITH_VARIANTS: "ok~ ",
    Verdict.TRUNCATED: "note",
    Verdict.ORDER_DIFFERS: "warn",
    Verdict.UNVERIFIABLE: "?   ",
    Verdict.PARTIAL_OVERLAP: "FAIL",
    Verdict.DISJOINT: "FAIL",
}

_HAND_CHECK = (
    "A DISJOINT verdict is a prompt to check the entry by hand. It is not an\n"
    "accusation of fabrication. Metadata is imperfect, author lists change\n"
    "between preprint and proceedings, and a search hit can be the wrong work."
)


# ---------------------------------------------------------------------------
# human report
# ---------------------------------------------------------------------------


def _author_lines(result: EntryResult, indent: str = "      ") -> List[str]:
    """Show the cited list against the record list, marking every finding."""
    out: List[str] = []
    entry = result.entry
    comparison = result.comparison
    if comparison is None:
        return out

    matched_cited = {ci: (ri, cmp) for ci, ri, cmp in comparison.pairs}
    record_authors = result.record.authors if result.record else []

    out.append("%scited (%d):" % (indent, len(entry.authors)))
    for index, name in enumerate(entry.authors):
        if index in matched_cited:
            record_index, comparison_detail = matched_cited[index]
            tags = ",".join(comparison_detail.variants)
            suffix = " -> [%d] %s" % (record_index, record_authors[record_index])
            if tags:
                suffix += "  (%s, %.2f)" % (tags, comparison_detail.score)
            out.append("%s  [%d] %s%s" % (indent, index, name, suffix))
        else:
            out.append("%s  [%d] %s   <-- NOT ON THIS WORK" % (indent, index, name))

    if comparison.unmatched_record:
        shown = comparison.unmatched_record[:8]
        out.append(
            "%srecord authors not cited (%d of %d):"
            % (indent, len(comparison.unmatched_record), len(record_authors))
        )
        for record_index in shown:
            out.append("%s  [%d] %s" % (indent, record_index, record_authors[record_index]))
        if len(comparison.unmatched_record) > len(shown):
            out.append(
                "%s  ... and %d more"
                % (indent, len(comparison.unmatched_record) - len(shown))
            )
    return out


def format_text(report: Report, verbose: bool = False, color: bool = False) -> str:
    """The human report. Pure ASCII, safe for any console and any CI log."""
    th = report.config.thresholds
    lines: List[str] = []
    counts = report.counts()

    lines.append("bibauthor: author-list verification")
    if report.sources:
        lines.append("  sources: %s" % ", ".join(report.sources))
    lines.append(
        "  entries: %d   providers: %s   mode: %s"
        % (
            len(report.results),
            ",".join(report.config.providers),
            "offline (cache only)" if report.config.offline else "online",
        )
    )
    lines.append("")

    ordered = sorted(
        report.results,
        key=lambda r: (-SEVERITY.get(r.verdict, 0), r.entry.key),
    )
    for result in ordered:
        severity = SEVERITY.get(result.verdict, 0)
        if not verbose and severity == 0 and not result.year_findings:
            continue
        mark = MARKS.get(result.verdict, "    ")
        source = result.record.source if result.record else "-"
        lines.append(
            "%s %-28s %-20s %s"
            % (mark, result.entry.label, result.verdict, "via %s" % source)
        )
        if result.entry.title:
            lines.append("      title: %s" % result.entry.title)
        if result.record and result.record.url:
            lines.append("      record: %s" % result.record.url)

        if result.comparison is not None and severity > 0:
            lines.extend(_author_lines(result))
            if result.comparison.order_differs:
                lines.append("      order differs from the record")
            if result.comparison.coverage < 1.0:
                lines.append(
                    "      coverage: %.0f%% of cited authors matched"
                    % (100.0 * result.comparison.coverage)
                )
            for note in result.comparison.notes:
                lines.append("      note: %s" % note)

        for finding in result.year_findings:
            lines.append("      year: %s" % finding.message)

        if verbose or result.verdict == Verdict.UNVERIFIABLE:
            for note in result.provider_notes:
                lines.append("      %s" % note)
        lines.append("")

    lines.append("summary")
    for verdict in Verdict.ALL:
        if counts.get(verdict):
            lines.append(
                "  %-20s %4d   %s"
                % (verdict, counts[verdict], DESCRIPTIONS[verdict])
            )
    year_total = sum(1 for r in report.results if r.year_findings)
    if year_total:
        lines.append("  %-20s %4d   date disagreements noted" % ("(year findings)", year_total))

    mismatches = report.mismatches
    unverifiable = report.unverifiable
    lines.append("")
    if mismatches:
        lines.append(
            "RESULT: %d entr%s with an author mismatch."
            % (len(mismatches), "y" if len(mismatches) == 1 else "ies")
        )
        lines.append("")
        for line in _HAND_CHECK.splitlines():
            lines.append(line)
    elif unverifiable:
        lines.append(
            "RESULT: no author mismatch found, but %d entr%s could not be checked."
            % (len(unverifiable), "y" if len(unverifiable) == 1 else "ies")
        )
    else:
        notes_present = year_total or any(
            SEVERITY.get(r.verdict, 0) > 0 for r in report.results
        )
        if notes_present:
            flagged = sum(
                1
                for r in report.results
                if SEVERITY.get(r.verdict, 0) > 0 or r.year_findings
            )
            lines.append(
                "RESULT: no author mismatch, but %d of %d entries carry a note "
                "worth reading." % (flagged, len(report.results))
            )
        else:
            lines.append("RESULT: clean. Every entry's author list matches its record.")

    lines.append("")
    lines.append(
        "thresholds: name_match=%.2f name_variant=%.2f title_similarity=%.2f "
        "partial_coverage=%.2f" % (
            th.name_match, th.name_variant, th.title_similarity, th.partial_coverage
        )
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def _comparison_json(result: EntryResult) -> Optional[Dict[str, Any]]:
    comparison = result.comparison
    if comparison is None:
        return None
    record_authors = result.record.authors if result.record else []
    return {
        "coverage": round(comparison.coverage, 4),
        "order_differs": comparison.order_differs,
        "truncated": comparison.truncated,
        "variants": sorted(set(comparison.variants)),
        "notes": list(comparison.notes),
        "matched": [
            {
                "cited_index": cited_index,
                "cited": result.entry.authors[cited_index],
                "record_index": record_index,
                "record": record_authors[record_index]
                if record_index < len(record_authors)
                else "",
                "score": round(detail.score, 4),
                "exact": detail.exact,
                "variants": list(detail.variants),
                "reason": detail.reason,
            }
            for cited_index, record_index, detail in comparison.pairs
        ],
        "unmatched_cited": [
            {"index": i, "name": result.entry.authors[i]}
            for i in comparison.unmatched_cited
        ],
        "unmatched_record": [
            {"index": i, "name": record_authors[i]}
            for i in comparison.unmatched_record
            if i < len(record_authors)
        ],
    }


def _record_json(record: Any) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    return {
        "source": record.source,
        "identifier": record.identifier,
        "title": record.title,
        "authors": list(record.authors),
        "year": record.year,
        "date": record.date,
        "first_version_year": record.first_version_year,
        "first_version_date": record.first_version_date,
        "doi": record.doi,
        "arxiv_id": record.arxiv_id,
        "url": record.url,
        "from_cache": record.from_cache,
        "confidence": round(record.confidence, 4),
        "notes": list(record.notes),
    }


def to_dict(report: Report) -> Dict[str, Any]:
    """The machine-readable report. This shape is the stable contract."""
    th = report.config.thresholds
    entries: List[Dict[str, Any]] = []
    for result in report.results:
        entries.append(
            {
                "key": result.entry.key,
                "entry_type": result.entry.entry_type,
                "source_file": result.entry.source_file,
                "source_line": result.entry.source_line,
                "title": result.entry.title,
                "cited_authors": list(result.entry.authors),
                "cited_year": result.entry.year,
                "cited_truncated": result.entry.truncated,
                "doi": result.entry.doi,
                "arxiv_id": result.entry.arxiv_id,
                "verdict": result.verdict,
                "verdict_description": DESCRIPTIONS.get(result.verdict, ""),
                "severity": SEVERITY.get(result.verdict, 0),
                "is_mismatch": is_mismatch(result.verdict, th),
                "record": _record_json(result.record),
                "comparison": _comparison_json(result),
                "year_findings": [
                    {
                        "kind": f.kind,
                        "cited_year": f.cited_year,
                        "record_year": f.record_year,
                        "first_version_year": f.first_version_year,
                        "message": f.message,
                    }
                    for f in result.year_findings
                ],
                "candidate_count": len(result.candidates),
                "providers_unavailable": list(result.unavailable),
                "provider_notes": list(result.provider_notes),
                "parse_warnings": list(result.entry.warnings),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "bibauthor",
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "sources": list(report.sources),
        "offline": report.config.offline,
        "providers": list(report.config.providers),
        "thresholds": dataclasses.asdict(th),
        "counts": report.counts(),
        "totals": {
            "entries": len(report.results),
            "mismatches": len(report.mismatches),
            "unverifiable": len(report.unverifiable),
            "year_findings": sum(1 for r in report.results if r.year_findings),
        },
        "exit_code": report.exit_code(),
        "disclaimer": (
            "A DISJOINT or PARTIAL_OVERLAP verdict is a prompt to check the "
            "entry by hand. It is not an accusation of fabrication."
        ),
        "entries": entries,
    }


def format_json(report: Report, indent: int = 2) -> str:
    """Serialize the report. ``ensure_ascii=True`` keeps output pipe-safe."""
    return json.dumps(
        to_dict(report), indent=indent, sort_keys=True, ensure_ascii=True
    ) + "\n"


def write_json(report: Report, stream: TextIO, indent: int = 2) -> None:
    stream.write(format_json(report, indent=indent))


def format_github_annotations(report: Report) -> str:
    """GitHub Actions workflow commands, so findings land on the diff.

    ``::error file=refs.bib,line=12::...`` renders as an annotation in the
    Actions UI and on the pull request.
    """
    th = report.config.thresholds
    lines: List[str] = []
    for result in report.results:
        severity = SEVERITY.get(result.verdict, 0)
        if severity == 0 and not result.year_findings:
            continue
        level = "error" if is_mismatch(result.verdict, th) else "warning"
        location = ""
        if result.entry.source_file:
            location = "file=%s" % result.entry.source_file
            if result.entry.source_line:
                location += ",line=%d" % result.entry.source_line
            location += ","
        detail = DESCRIPTIONS.get(result.verdict, "")
        unmatched = ""
        if result.comparison and result.comparison.unmatched_cited:
            names = [
                result.entry.authors[i]
                for i in result.comparison.unmatched_cited[:4]
            ]
            unmatched = " Not on this work: %s." % "; ".join(names)
        message = "%s: %s (%s).%s" % (
            result.entry.key,
            result.verdict,
            detail,
            unmatched,
        )
        for finding in result.year_findings:
            message += " Year: %s." % finding.message
        lines.append(
            "::%s %stitle=bibauthor::%s"
            % (level, location, message.replace("\n", " "))
        )
    return "\n".join(lines) + ("\n" if lines else "")
