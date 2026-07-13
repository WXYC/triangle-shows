"""
Best-effort headliner extraction from event billing strings (issue #18).

Role: Derives the nullable ``headliner`` field — the performer with support acts,
ticketing tags, and framing stripped — that the events API exposes alongside the
untouched ``name``/``artist``. The scrape manager calls extract_headliner() at
upsert time on the scraper-supplied structured performer (schema.org
``Event.performer``, Ticketmaster attractions) when one exists, else on the raw
event name. Consumed by scrapers/manager.py; scrapers themselves only pass
structured performer data through (ScrapedEvent.headliner).
Requires: pure Python stdlib (re) only.

Philosophy: conservative — prefer under-stripping. The downstream consumer (WXYC
Backend-Service's touring-events resolver, WXYC/Backend-Service#1604) exact-matches
this string against a music-library catalog, so a missed strip merely fails to
match while an over-strip fabricates a wrong artist. Concretely:

- "&"/"and" are NEVER support delimiters ("Andy Frasco & The U.N" is one act), and
  neither is a bare "with" ("with special guest(s)" is the only "with" form cut).
- Leading parenthetical/bracket tags strip only when their content looks like
  ticketing/venue noise (a recognized keyword or an age gate) — "(Sandy) Alex G"
  survives.
- Non-performance framings (karaoke, listening party, trivia, ...) and cleanups
  that empty the string yield None: a null headliner beats a fabricated one.
- No spelling correction, ever — the source's own rendering is returned.
"""

import re
from typing import Optional

_WS_RE = re.compile(r"\s+")

# A leading "(...)" or "[...]" tag (no nesting — venue tags never nest).
_LEADING_TAG_RE = re.compile(r"^[(\[]([^)\]]*)[)\]]\s*")

# Age gates like "18+" / "21 +" anywhere in a tag's content.
_AGE_GATE_RE = re.compile(r"\b\d{1,2}\s*\+")

# Ticketing/venue noise vocabulary for parenthetical tags. Matched with word
# boundaries inside the tag content only — never against the billing itself.
_TAG_KEYWORD_RE = re.compile(
    r"\b(?:sold out|selling fast|low (?:tix|tickets?)|just added|on sale|"
    r"free(?: show)?|cancell?ed|postponed|rescheduled|moved|new (?:date|venue|time)|"
    r"all ages|seated|standing(?: room)?(?: only)?|outdoors?|patio|record shop|"
    r"early show|late show|matinee|second show|two shows|presented by)\b",
    re.IGNORECASE,
)

# Framing prefixes. "presents" requires the colon — a bare "X presents Y" is too
# ambiguous to cut conservatively.
_EVENING_WITH_RE = re.compile(r"^an evening with:?\s+", re.IGNORECASE)
_PRESENTS_RE = re.compile(r"\bpresents?\s*:\s*", re.IGNORECASE)

# Support-act tails. "w/" tolerates a missing space after the slash but must not
# fire on "w/o" or its long form "w/out" (nor "without"); "//" requires leading
# whitespace so it can't split a URL-ish name.
_SUPPORT_TAIL_RES = (
    re.compile(r"\s+w/(?!o(?:ut)?\b)\s*", re.IGNORECASE),
    re.compile(r"\s+//\s*"),
    re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE),
    re.compile(r"\s+with special guests?\b", re.IGNORECASE),
)

# The four support-tail delimiters as ONE non-capturing alternation, so a single
# re.split cuts on whichever fires leftmost and — crucially — keeps every piece
# after it (an interleaved "A w/ B // C" yields ["A", "B", "C"], where the old
# per-delimiter loop kept only "A"). Each sub-pattern is wrapped in (?:…) and joined
# with "|"; (?:…) means re.split does NOT emit the delimiter text as list elements.
# Built from _SUPPORT_TAIL_RES verbatim so the grammar can't drift from extract's.
# Every sub-pattern is case-insensitive, so the combined pattern carries IGNORECASE.
_COMBINED_SUPPORT_TAIL_RE = re.compile(
    "|".join(f"(?:{r.pattern})" for r in _SUPPORT_TAIL_RES), re.IGNORECASE
)

# Events where nobody on the marquee is performing.
_NON_PERFORMANCE_RE = re.compile(
    r"\b(?:karaoke|listening party|trivia|bingo|open mic(?:rophone)?)\b",
    re.IGNORECASE,
)

# "X: A Tribute to Y" — the honoree Y is not playing; the tribute act X (when
# named) is. Only the explicit "tribute to" phrasing is handled.
_TRIBUTE_RE = re.compile(
    r"^(?P<act>.*?)(?:\s*[:\-–—]\s*)?\b(?:an?\s+)?(?:live\s+)?tribute to\b",
    re.IGNORECASE,
)


# Separator/filler characters that carry no band-name signal, so a keyword tag
# padded with them ("LOW TIX!", "SOLD-OUT") still counts as keyword-dominated.
_TAG_FILLER_RE = re.compile(r"[\s\-–—:;,.!/&+]+")


def _is_noise_tag(content: str) -> bool:
    """Is this parenthetical/bracket content ticketing noise (vs a band name)?

    Conservative on purpose: a leading parenthetical is only noise when it is
    *recognizably* ticketing/venue framing, so a band name that happens to open
    a billing in parentheses — "(Free Energy) Truth Club", "(Sandy) Alex G" —
    survives. Three rules qualify a tag as noise:

    - Empty ("()") — carries nothing worth keeping.
    - An age gate anywhere ("18+", "21 +").
    - A ticketing/venue keyword that *dominates* the tag: once every matched
      keyword phrase and all separator/filler characters are removed, nothing
      band-like is left ("(SOLD OUT)", "(LOW TIX)", "(Record Shop)", "(Seated)").
      Merely *containing* a common word is not enough — "(Free Energy)" keeps
      "Energy" after the "Free" match, so it is a name, not noise.
    """
    content = content.strip()
    if not content:
        return True  # "()" carries nothing worth keeping
    if _AGE_GATE_RE.search(content):
        return True
    if not _TAG_KEYWORD_RE.search(content):
        return False
    # Keyword-dominance: strip every keyword match, then the filler around them.
    # A residue means the tag also names something (a band), so it isn't noise.
    residue = _TAG_KEYWORD_RE.sub("", content)
    residue = _TAG_FILLER_RE.sub("", residue)
    return not residue


def parse_billing(billing: Optional[str]) -> tuple[Optional[str], list[str]]:
    """Split a billing string into ``(headliner, tail_support_names)``.

    The single grammar behind both fields (issue #41): ``extract_headliner`` returns
    ``parse_billing(b)[0]`` so the headliner derivation stays byte-identical, while
    the second element recovers the support acts that live in a billing tail
    ("King Serpent w/ Booster Club, Field Day" -> ``["Booster Club", "Field Day"]``)
    which the old headliner cut discarded.

    Steps (1-2 and 4-5 mirror the historical ``extract_headliner`` exactly; step 3 is
    the one behavioral gain — it keeps the tail instead of dropping it):

    1. Collapse whitespace; blank input -> ``(None, [])``.
    2. Strip leading noise tags ("(SOLD OUT)") and framing ("An Evening With:",
       "<presenter> Presents:") -> ``core``.
    3. Split ``core`` on the combined support-tail alternation. The FIRST piece is the
       headliner prefix; every later piece is a support segment. Splitting on the
       combined pattern (vs the old per-delimiter loop) is what preserves interleaved
       tails — "A w/ B // C" yields prefix "A" and segments ["B", "C"].
    4. Build the tail: split each support segment on commas (commas split ONLY inside
       tails, never the headliner prefix), strip each name, drop empties, left to right.
    5. Derive the headliner from the prefix: a non-performance framing (karaoke,
       listening party) or a bare "Tribute to Y" nulls it, then a tribute act before
       the framing is recovered, then a final edge-punctuation tidy. May be ``None``.

    Support is DECOUPLED from a null headliner: a cleanly delimited act is independently
    billed and performing, so "Cat Power Listening Party w/ DJ X" -> ``(None, ["DJ X"])``.
    Conservative, matching extraction: "&"/"and"/bare "with" are never delimiters, and
    no spelling correction — the source's own rendering is returned.
    """
    if not billing:
        return None, []
    text = _WS_RE.sub(" ", billing).strip()

    # 1. Leading "(SOLD OUT)"/"[18+]"-style tags, possibly chained. Stops at the
    #    first parenthetical that is NOT recognized noise, so "(Sandy) Alex G"
    #    keeps its name.
    while (match := _LEADING_TAG_RE.match(text)) and _is_noise_tag(match.group(1)):
        text = text[match.end():]

    # 2. Framing prefixes: "An Evening With: X", "<presenter> Presents: X".
    text = _EVENING_WITH_RE.sub("", text)
    presents = _PRESENTS_RE.search(text)
    if presents:
        text = text[presents.end():]

    # 3. Support-act tails: one leftmost-any-delimiter split. pieces[0] is the
    #    headliner prefix; pieces[1:] are the support segments the old sequential
    #    loop discarded (the interleaved-billing fix).
    pieces = _COMBINED_SUPPORT_TAIL_RE.split(text)
    headliner_prefix = pieces[0]
    tail_pieces = pieces[1:]

    # 4. Tail support: commas split WITHIN a segment (only here, never the prefix),
    #    strip each name, drop empties, left to right.
    tail_support: list[str] = []
    for piece in tail_pieces:
        for name in piece.split(","):
            name = name.strip()
            if name:
                tail_support.append(name)

    # 5. Headliner, derived from the prefix (exactly the historical steps 4-5).
    #    Non-performance events have no headliner at all — null, never a guess
    #    (a listening party's honoree is not on stage). Support already captured above.
    if _NON_PERFORMANCE_RE.search(headliner_prefix):
        return None, tail_support

    # Tribute framing: the act before "…: A Tribute to Y" is the performer; a bare
    # "Tribute to Y" names nobody who is actually playing.
    tribute = _TRIBUTE_RE.match(headliner_prefix)
    if tribute:
        headliner_prefix = tribute.group("act")

    # Final tidy: drop whitespace and any delimiter left dangling by a strip. The
    # strip set is punctuation-only-at-the-edges — symbol names like "Sunn O)))"
    # are untouched.
    headliner = headliner_prefix.strip(" \t:;,-–—|") or None
    return headliner, tail_support


def extract_headliner(billing: Optional[str]) -> Optional[str]:
    """Extract the clean headliner from a billing string, or None.

    Returns the performer with leading noise tags, framing prefixes, support-act
    tails, and tribute framing stripped. Returns None when nothing performer-like
    remains: blank input, tag-only billings, non-performance events (karaoke,
    listening parties, ...), or a "Tribute to X" with no named tribute act.
    A string with nothing to strip is returned as-is (whitespace-collapsed).

    Thin projection of :func:`parse_billing` — one shared grammar, so this field and
    the derived support list can never drift apart.
    """
    return parse_billing(billing)[0]


def merge_support(structured: list[str], tail: list[str], headliner: Optional[str]) -> list[str]:
    """Union a scraper's structured performer list with a parsed billing tail (issue #41).

    Produces the stored ``support_artists``: ``structured`` names first (in source
    order), then ``tail`` names (left to right), with the headliner excluded and
    duplicates dropped. Pure and deterministic — identical inputs yield byte-identical
    output (no sorting), so a re-scrape doesn't spuriously stamp ``updated_at``.

    Each name is whitespace-normalized before use — outer whitespace trimmed and internal
    runs collapsed to single spaces, the SAME normalization the manager applies to the
    headliner (``" ".join(structured.split())``) — and empty/whitespace-only names (and a
    ``None`` a scraper may leak for a missing schema.org performer ``name``) are dropped.
    Without this a blank structured performer would store ``[""]`` (the old comma-join +
    split path dropped it), and an irregular-whitespace rendering would dodge both the
    dedupe and the headliner exclusion.

    Dedupe and exclusion are **casefold-only**: the first occurrence keyed by
    ``name.casefold()`` wins (so a structured rendering beats a tail dupe of the same
    act), and any name whose casefold equals the headliner's is dropped. Deliberately
    NO accent/punctuation/"The" folding — the corpus carries distinct diacritic-bearing
    acts ("Nilüfer Yanya"), and over-folding would silently drop a real opener. Names
    are treated as atomic: a comma inside a structured name ("Earth, Wind & Fire") is
    never split.
    """
    excluded = headliner.casefold() if headliner is not None else None
    seen: set[str] = set()
    merged: list[str] = []
    for name in [*structured, *tail]:
        cleaned = " ".join(name.split()) if name else ""
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key == excluded or key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)
    return merged
