"""The Verifier: plain Python, no database, no LLM. It blocks claims that have no evidence.

A claim is a dict: {"expert_id": "A..", "text": "...", "kind": "tie" | "no_tie",
                    "evidence_refs": ["10.1/abc", ...],               # tie claims
                    "query_name": "q3_bfs_v1", "scope": {"hops":..,"years":..,"coverage":..,"funder_coverage":..}}  # no_tie
`kind` may be left out: a claim that carries a query_name or a scope is treated as "no_tie".

Rules (CLAUDE.md, redesign section 3):
  1. A tie claim needs at least one evidence ref, and every ref must be a DOI that is in the expert's stored evidence.
  2. A no-tie claim needs a known query_name plus a scope with hops, years, coverage and funder_coverage
     (funder-only share of topic works; added Oct 16 by Aaryan, because coverage also counts affiliations).
  3. The words "independent" and "conflicted" are never allowed in the text (since Oct 16 also "unbiased" and
     "clean source": editorialising seen in the 5f run; decided by Aaryan).
Checkpoint 7 (draft_note, spec 9f): the server fills these optional keys from its own fresh run, never from the
Writer's input, so a claim cannot bring its own scope:
  - "result" (no-tie claims): check_no_tie's result; anything but NO_TIE_FOUND rejects the claim;
  - "label": the expert's label; another "Expert #n" in the text rejects the claim;
  - "forbidden_names": names that must not appear (the case's experts, in a case that is not public);
  - "evidence_refs" on a no-tie claim: only its query_name may be cited there.
"""
import re
import unicodedata

# Whole words (after norm_text). "clean source" also matches "clean-source(s)". Since the checkpoint-7 review
# (Aaryan, Oct 16): "unbiased", "unbiassed", "impartial" and "clean source" are editorialising, like the first two.
BANNED_WORDS = {"independent": r"independents?", "conflicted": r"conflicted", "unbiased": r"unbiased",
                "unbiassed": r"unbiassed", "impartial": r"impartial", "clean source": r"clean[\s-]+sources?"}
# Decision (Oct 15, Abdul): also block "independence" and "independently". They make the same claim, and
# the old tool was called prove_independence. Whole-word match only, so "interdependent" stays allowed.
# "unconflicted" (review, Oct 16) is the same claim turned around.
CLAIM_WORDS = ("independence", "independently", "unconflicted")
KNOWN_QUERIES = {"q3_bfs_v1"}              # query names the server stores (see research.check_no_tie)
SCOPE_FIELDS = ("hops", "years", "coverage", "funder_coverage")
# A no-tie claim's text must not assert a tie, and a tie claim's text must not deny one (draft_note only).
# A draft_note no-tie text may only be this sentence (any case, spacing or accents): a word list of "tie words"
# kept missing phrasings (review round 2), so only the template is accepted. The server then stores its own
# sentence for every claim anyway (server._claim_sentence), so the Writer's wording never reaches the note.
NO_TIE_TEMPLATE = re.compile(r"no tie found in the research record(?: for expert #\d+)?(?: within the stated scope)?"
                             r"(?:[.;,] ?absence(?: in the research record)? is not proof)?\.?")
TIE_WORDS = re.compile(r"\b(funded|paid|payments?|affiliated|employed|employee|employs|sponsored|received|"
                       r"receives|consultant|consulted|consults|grantee)\b")
NO_TIE_WORDS = re.compile(r"\bno ties?\b")
# Look-alike letters people (or models) use to dodge a word filter: Cyrillic and Greek to Latin.
HOMOGLYPHS = str.maketrans("аеорсухіјѕԁԛԝАЕОРСУХІЈЅοεικνρτυαΟΕΙΚΝΡΤΥΑ", "aeopcyxijsdqwaeopcyxijsoeikvptuaoeikvptua")


def norm_doi(doi):
    """'https://doi.org/10.1/AB' -> '10.1/ab' (the form stored in the graph)."""
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", str(doi).strip().lower())


def fold(text):
    """Lower case without accents, so 'José' and 'jose' match."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c)).lower()


def norm_text(text):
    """The form every word check runs on: NFKC (full-width letters become plain), zero-width and other format
    characters removed, look-alike letters mapped to Latin, accents dropped, lower case (fold's NFKD also
    turns full-width letters into plain ones)."""
    text = "".join(c for c in str(text) if unicodedata.category(c) != "Cf").translate(HOMOGLYPHS)
    return fold(text)


def _words_only(text):
    """Letters and digits only, single spaces: 'J. O. Hill' -> 'j o hill'."""
    return " ".join(re.findall(r"[a-z0-9]+", norm_text(text)))


def _name_forms(name):
    """The ways a name can appear: full, surname (3+ letters), first + surname, initial + surname."""
    words = _words_only(name).split()
    if not words:
        return []
    forms = {" ".join(words)}
    if len(words) > 1:
        first, last = words[0], words[-1]
        forms |= {f"{first} {last}", f"{first[0]} {last}"}
        if len(last) >= 3:
            forms.add(last)
    return sorted(forms)


def text_problems(text, forbidden_names=()):
    """Banned words and names in any text (a claim, or the case's story in the note header)."""
    reasons, t = [], norm_text(text)
    for word, pattern in BANNED_WORDS.items():
        if re.search(rf"\b{pattern}\b", t):
            reasons.append(f"banned word '{word}'; say 'ties found in the research record'")
    for word in CLAIM_WORDS:
        if re.search(rf"\b{word}\b", t):
            reasons.append(f"avoid '{word}'; say 'ties found in the research record'")
    plain = _words_only(text)
    collapsed = re.sub(r"\b[a-z](?: [a-z]\b)+", lambda m: m.group(0).replace(" ", ""), plain)  # "h i l l" -> "hill"
    forms = {f for n in forbidden_names or [] if n for f in _name_forms(n)}
    if any(re.search(rf"\b{re.escape(f)}\b", x) for f in forms for x in (plain, collapsed)):
        reasons.append("the text names a case expert; experts are 'Expert #n' unless the case is public")
    return reasons


def verify_claim(claim, stored_dois_for_expert):
    """Return a list of reasons this claim is rejected (empty list = it passes)."""
    text = str(claim.get("text") or "")
    reasons = text_problems(text, claim.get("forbidden_names"))
    label = claim.get("label")
    if isinstance(label, str) and re.fullmatch(r"Expert #\d+", label):
        mentioned = {f"Expert #{n}" for n in re.findall(r"\bexpert\s*(?:#|no\.?|number)?\s*(\d+)", norm_text(text))}
        others = sorted(mentioned - {label})
        if others:
            reasons.append(f"the claim is about {label} but the text mentions {', '.join(others)}")

    is_no_tie = claim.get("kind") == "no_tie" or (
        claim.get("kind") is None and ("query_name" in claim or "scope" in claim))
    if "result" in claim:  # set by draft_note only: the text must say what the evidence says
        t = norm_text(text)
        if is_no_tie and (not NO_TIE_TEMPLATE.fullmatch(" ".join(t.split())) or TIE_WORDS.search(t)):
            reasons.append("a no-tie claim says only: 'No tie found in the research record for Expert #n within "
                           "the stated scope; absence is not proof.' (a tie needs a tie claim with a DOI)")
        if not is_no_tie and NO_TIE_WORDS.search(t):
            reasons.append("a tie claim cites DOIs, so its text must not say 'no tie'")
    result = str(claim.get("result", "NO_TIE_FOUND"))
    if is_no_tie and result.startswith("ERROR"):
        reasons.append(f"check_no_tie failed ({result[6:]}), so no no-tie claim can be checked; try again later")
    elif is_no_tie and result != "NO_TIE_FOUND":
        reasons.append(f"check_no_tie found a tie for this expert ({result}); a no-tie claim is not allowed")
    elif is_no_tie:
        extra = [r for r in claim.get("evidence_refs") or [] if r != claim.get("query_name")]
        if extra:
            reasons.append("a no-tie claim cites only its query_name; put DOIs in a separate tie claim")
        if claim.get("query_name") not in KNOWN_QUERIES:
            reasons.append(f"no-tie claim needs a stored query_name (one of {sorted(KNOWN_QUERIES)})")
        scope = claim.get("scope")
        if not isinstance(scope, dict):
            reasons.append("no-tie claim needs a scope with hops, years, coverage and funder_coverage")
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
