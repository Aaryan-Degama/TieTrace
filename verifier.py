"""The Verifier: plain Python, no database, no LLM. It blocks claims that have no evidence.

A claim is a dict: {"expert_id": "A..", "text": "...", "kind": "tie" | "no_tie",
                    "evidence_refs": ["10.1/abc", ...],               # tie claims
                    "query_name": "q3_bfs_v1", "scope": {"hops":..,"years":..,"coverage":..}}  # no_tie claims
`kind` may be left out: a claim that carries a query_name or a scope is treated as "no_tie".

Rules (CLAUDE.md, redesign section 3):
  1. A tie claim needs at least one evidence ref, and every ref must be a DOI that is in the expert's stored evidence.
  2. A no-tie claim needs a known query_name plus a scope with hops, years and coverage.
  3. The words "independent" and "conflicted" are never allowed in the text.
"""
import re

BANNED_WORDS = ("independent", "conflicted")
# Decision (Oct 15, Abdul): also block "independence" and "independently". They make the same claim, and
# the old tool was called prove_independence. Whole-word match only, so "interdependent" stays allowed.
CLAIM_WORDS = ("independence", "independently")
KNOWN_QUERIES = {"q3_bfs_v1"}              # query names the server stores (see research.check_no_tie)
SCOPE_FIELDS = ("hops", "years", "coverage")


def norm_doi(doi):
    """'https://doi.org/10.1/AB' -> '10.1/ab' (the form stored in the graph)."""
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", str(doi).strip().lower())


def verify_claim(claim, stored_dois_for_expert):
    """Return a list of reasons this claim is rejected (empty list = it passes)."""
    reasons = []
    text = str(claim.get("text") or "")
    for word in BANNED_WORDS:
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):  # whole word, any case
            reasons.append(f"banned word '{word}'; say 'ties found in the research record'")

    for word in CLAIM_WORDS:
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            reasons.append(f"avoid '{word}'; say 'ties found in the research record'")

    is_no_tie = claim.get("kind") == "no_tie" or (
        claim.get("kind") is None and ("query_name" in claim or "scope" in claim))
    if is_no_tie:
        if claim.get("query_name") not in KNOWN_QUERIES:
            reasons.append(f"no-tie claim needs a stored query_name (one of {sorted(KNOWN_QUERIES)})")
        scope = claim.get("scope")
        if not isinstance(scope, dict):
            reasons.append("no-tie claim needs a scope with hops, years and coverage")
        else:
            missing = [f for f in SCOPE_FIELDS if scope.get(f) in (None, "")]
            if missing:
                reasons.append("no-tie claim scope is missing: " + ", ".join(missing))
    else:
        refs = claim.get("evidence_refs") or []
        stored = {norm_doi(d) for d in stored_dois_for_expert or []}
        if not refs:
            reasons.append("tie claim has no evidence_refs; a positive claim needs a stored DOI")
        for ref in refs:
            if norm_doi(ref) not in stored:
                reasons.append(f"evidence ref '{ref}' is not a stored DOI for this expert")
    return reasons


def verify_claims(claims, stored_dois_by_expert):
    """Check every claim. stored_dois_by_expert = {expert_id: [doi, ...]}.
    Returns {"status": "verified" | "rejected", "rejections": [{claim_index, reason, code}]}."""
    rejections = []
    for i, claim in enumerate(claims):
        stored = stored_dois_by_expert.get(claim.get("expert_id"), [])
        for reason in verify_claim(claim, stored):
            rejections.append({"claim_index": i, "reason": reason, "code": "VERIFIER_REJECT"})
    return {"status": "rejected" if rejections else "verified", "rejections": rejections}
