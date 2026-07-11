"""Unit tests for best-effort headliner extraction (issue #18).

Pure-function tests — no database or HTTP client. The philosophy under test is
conservative under-stripping: a missed strip merely fails to resolve downstream,
while an over-strip fabricates a wrong artist. Every strip pattern gets a positive
case and the risky look-alikes ("&"/"and" in band names, parenthetical band names)
get explicit survival cases.
"""

import pytest

from app.scrapers.headliner import extract_headliner


# --- Support-act tails ---

@pytest.mark.parametrize(
    ("billing", "expected"),
    [
        # "w/" tail — and no spelling correction: the source's apostrophe stays.
        ("Acid Mother's Temple w/ Magick Potion", "Acid Mother's Temple"),
        ("Wednesday w/ MJ Lenderman", "Wednesday"),
        # No space after the slash is a common venue-site shape.
        ("Wednesday w/MJ Lenderman", "Wednesday"),
        ("Juana Molina w/ Special Interest & Truth Club", "Juana Molina"),
        # "//" support separators.
        ("Mdou Moctar // Nilüfer Yanya // Truth Club", "Mdou Moctar"),
        # feat./ft./featuring tails.
        ("Jessica Pratt feat. Ryley Walker", "Jessica Pratt"),
        ("Deerhoof featuring Saya Gray", "Deerhoof"),
        ("Kelela ft. Yves Tumor", "Kelela"),
        # "with special guest(s)" — but never a bare "with" (see survival cases).
        ("Chuquimamani-Condori with special guests", "Chuquimamani-Condori"),
        ("Stereolab with special guest Fievel Is Glauque", "Stereolab"),
    ],
)
def test_strips_support_act_tails(billing, expected):
    assert extract_headliner(billing) == expected


# --- "w/" tail must not fire on "w/o" or "w/out"/"without" ---

@pytest.mark.parametrize(
    "name",
    [
        # "w/o" (short) and "w/out" (long form) are part of the name, not a support
        # tail — the "w/" delimiter must skip both, and plain "without" too.
        "Angel w/o Wings",
        "The Man w/out a Country",
        "Nothing without You",
    ],
)
def test_w_slash_o_forms_are_not_support_tails(name):
    assert extract_headliner(name) == name


def test_w_slash_still_strips_a_real_support_tail():
    # The guard for "w/o"/"w/out" must not disable the ordinary "w/ Support" cut.
    assert extract_headliner("Truth Club w/ Weak Signal") == "Truth Club"


# --- Leading ticketing/venue tags ---

@pytest.mark.parametrize(
    ("billing", "expected"),
    [
        ("(SOLD OUT) Jessica Pratt", "Jessica Pratt"),
        ("(LOW TIX) (18+) Mdou Moctar", "Mdou Moctar"),
        ("[CANCELLED] Stereolab", "Stereolab"),
        ("(21+) Hermanos Gutiérrez", "Hermanos Gutiérrez"),
        ("(FREE SHOW) Truth Club", "Truth Club"),
        # A tag that is ENTIRELY a keyword ("Seated") is keyword-dominated, so it
        # strips — the residue after removing the keyword and filler is empty.
        ("(Seated) Some Band", "Some Band"),
        # A name that is nothing but a tag cleans down to nothing.
        ("(SOLD OUT)", None),
    ],
)
def test_strips_leading_noise_tags(billing, expected):
    assert extract_headliner(billing) == expected


# --- Framing prefixes ---

@pytest.mark.parametrize(
    ("billing", "expected"),
    [
        ("An Evening With: Mountain Grass Unit", "Mountain Grass Unit"),
        ("An Evening with Cat Power", "Cat Power"),
        ("WXYC 89.3 Presents: Stereolab", "Stereolab"),
        ("Cat's Cradle Presents: Nilüfer Yanya", "Nilüfer Yanya"),
        # Tags and framing chain.
        ("(SOLD OUT) An Evening With: Jessica Pratt", "Jessica Pratt"),
    ],
)
def test_strips_framing_prefixes(billing, expected):
    assert extract_headliner(billing) == expected


# --- "Tribute to …" framing ---

@pytest.mark.parametrize(
    ("billing", "expected"),
    [
        # The honoree is not the performer — with no tribute-act prefix there is
        # no extractable headliner.
        ("Tribute to Duke Ellington", None),
        ("A Tribute to John Coltrane", None),
        # A named tribute act before the framing IS the performer.
        ("Giant Steps: A Tribute to John Coltrane", "Giant Steps"),
        ("Trane Fest - A Tribute to John Coltrane", "Trane Fest"),
    ],
)
def test_tribute_framing(billing, expected):
    assert extract_headliner(billing) == expected


# --- Non-performance events ---

@pytest.mark.parametrize(
    "billing",
    [
        "WEDNESDAY KARAOKE!",
        "Vinyl Listening Party",
        # Tag-stripping must not rescue a non-performance event into a fake artist:
        # Cat Power is not performing at her own listening party.
        "(Record Shop) Cat Power Listening Party",
        "Open Mic Night",
        "Music Trivia",
        "Music Bingo",
    ],
)
def test_non_performance_events_yield_null(billing):
    assert extract_headliner(billing) is None


# --- Survival cases: names that must pass through untouched ---

@pytest.mark.parametrize(
    "name",
    [
        # "&"/"and" are never support delimiters — they live inside band names.
        "Andy Frasco & The U.N",
        "Duke Ellington & John Coltrane",
        "Iron and Wine",
        # Parenthetical band names survive: only recognized noise tags strip.
        "(Sandy) Alex G",
        # A leading parenthetical BAND name survives even when it contains a word
        # that also appears in the ticketing vocabulary ("Free"): the keyword must
        # DOMINATE the tag to count as noise, and "Free Energy" leaves "Energy".
        "(Free Energy) Truth Club",
        # Trailing symbols are part of the name, not dangling punctuation.
        "Sunn O)))",
        # A plain "with"/"w"-word is not the "w/" delimiter.
        "Jeffrey Lewis & The Voltage",
        "Florry and the Wandering Kind",
        "Csillagrablók",
        "Juana Molina",
    ],
)
def test_clean_names_pass_through_unchanged(name):
    assert extract_headliner(name) == name


# --- Degenerate input ---

@pytest.mark.parametrize("billing", [None, "", "   "])
def test_blank_input_yields_null(billing):
    assert extract_headliner(billing) is None


def test_whitespace_is_collapsed():
    assert extract_headliner("  Jessica   Pratt  ") == "Jessica Pratt"
