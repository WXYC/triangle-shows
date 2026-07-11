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
# fire on "w/o"; "//" requires leading whitespace so it can't split a URL-ish name.
_SUPPORT_TAIL_RES = (
    re.compile(r"\s+w/(?!o\b)\s*", re.IGNORECASE),
    re.compile(r"\s+//\s*"),
    re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE),
    re.compile(r"\s+with special guests?\b", re.IGNORECASE),
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


def _is_noise_tag(content: str) -> bool:
    """Is this parenthetical/bracket content ticketing noise (vs a band name)?"""
    content = content.strip()
    if not content:
        return True  # "()" carries nothing worth keeping
    return bool(_AGE_GATE_RE.search(content) or _TAG_KEYWORD_RE.search(content))


def extract_headliner(billing: Optional[str]) -> Optional[str]:
    """Extract the clean headliner from a billing string, or None.

    Returns the performer with leading noise tags, framing prefixes, support-act
    tails, and tribute framing stripped. Returns None when nothing performer-like
    remains: blank input, tag-only billings, non-performance events (karaoke,
    listening parties, ...), or a "Tribute to X" with no named tribute act.
    A string with nothing to strip is returned as-is (whitespace-collapsed).
    """
    if not billing:
        return None
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

    # 3. Support-act tails: keep everything before the earliest delimiter.
    for tail_re in _SUPPORT_TAIL_RES:
        text = tail_re.split(text, maxsplit=1)[0]

    # 4. Non-performance events have no headliner at all — null, never a guess
    #    (a listening party's honoree is not on stage).
    if _NON_PERFORMANCE_RE.search(text):
        return None

    # 5. Tribute framing: the act before "…: A Tribute to Y" is the performer;
    #    a bare "Tribute to Y" names nobody who is actually playing.
    tribute = _TRIBUTE_RE.match(text)
    if tribute:
        text = tribute.group("act")

    # Final tidy: drop whitespace and any delimiter left dangling by a strip.
    # The strip set is punctuation-only-at-the-edges — symbol names like
    # "Sunn O)))" are untouched.
    text = text.strip(" \t:;,-–—|")
    return text or None
