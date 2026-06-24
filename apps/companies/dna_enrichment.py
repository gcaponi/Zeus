"""Central enrichment orchestrator — computes the full cognitive bundle
for a DNA and packs it into the _enrichment JSON field on CompanyDNA.

This is the single integration point the pipeline calls: instead of each
view/task importing validator, scoring and evidence separately, they call
build_enrichment(content, available_sources) and store the result.

The bundle is a plain dict, JSON-serializable, structured so the shadow
test and future admin views can read every diagnostic in one place.
"""
from __future__ import annotations

from typing import Any

from apps.companies.dna_scoring import score_dna
from apps.companies.dna_validator import validate_dna
from apps.companies.evidence import check_source_consistency


def build_enrichment(
    content: Any,
    available_sources: dict | None = None,
) -> dict:
    """Compute the full cognitive enrichment bundle for a DNA payload.

    Args:
        content: the DNA content dict (as stored in CompanyDNA.content).
        available_sources: optional dict for evidence consistency:
            {"scrape": bool, "note": bool, "files": [str]}.
            When None, evidence mismatches on unavailable categories are
            not checked (only unknown categories / missing files).

    Returns:
        {
            "validation": {valid, score, guards_passed, guards_total,
                           safe_mode, flags: [{guard, severity, layer, ...}]},
            "scoring": {completeness, cognitive_tension, evidence_density,
                        source_diversity, confidence, overall},
            "evidence": {has_mismatch, mismatches: [{kind, detail, marker}],
                         source_count, categories},
        }
    """
    # --- Validation ---
    val = validate_dna(content)
    validation = {
        "valid": val.valid,
        "score": val.score,
        "guards_passed": val.guards_passed,
        "guards_total": val.guards_total,
        "safe_mode": val.safe_mode,
        "flags": [
            {
                "guard": f.guard,
                "severity": f.severity,
                "layer": f.layer,
                "message": f.message,
                "suggestion": f.suggestion,
            }
            for f in val.flags
        ],
    }

    # --- Scoring ---
    sc = score_dna(content)
    scoring = {
        "completeness": sc.completeness,
        "cognitive_tension": sc.cognitive_tension,
        "evidence_density": sc.evidence_density,
        "source_diversity": sc.source_diversity,
        "confidence": sc.confidence,
        "overall": sc.overall,
    }

    # --- Evidence ---
    available = available_sources or {}
    ev = check_source_consistency(content, available)
    evidence = {
        "has_mismatch": ev.has_mismatch,
        "mismatches": [
            {"kind": m.kind, "detail": m.detail, "marker": m.marker}
            for m in ev.mismatches
        ],
        "source_count": len(ev.sources),
        "categories": sorted({s.category for s in ev.sources}),
    }

    return {
        "validation": validation,
        "scoring": scoring,
        "evidence": evidence,
    }


def enrichment_summary(enrichment: dict | None) -> dict:
    """Flatten the enrichment bundle into a compact summary for display.

    Returns the few numbers that matter for a quick health read.
    """
    if not enrichment:
        return {"available": False}
    v = enrichment.get("validation", {})
    s = enrichment.get("scoring", {})
    e = enrichment.get("evidence", {})
    return {
        "available": True,
        "valid": v.get("valid"),
        "safe_mode": v.get("safe_mode"),
        "validation_score": v.get("score"),
        "overall_score": s.get("overall"),
        "cognitive_tension": s.get("cognitive_tension"),
        "confidence": s.get("confidence"),
        "evidence_mismatch": e.get("has_mismatch"),
        "flag_count": len(v.get("flags", [])),
    }
