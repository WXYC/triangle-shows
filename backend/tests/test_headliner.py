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


# --- Leading ticketing/venue tags ---

@pytest.mark.parametrize(
    ("billing", "expected"),
    [
        ("(SOLD OUT) Jessica Pratt", "Jessica Pratt"),
        ("(LOW TIX) (18+) Mdou Moctar", "Mdou Moctar"),
        ("[CANCELLED] Stereolab", "Stereolab"),
        ("(21+) Hermanos Gutiérrez", "Hermanos Gutiérrez"),
        ("(FREE SHOW) Truth Club", "Truth Club"),
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
        ("A Tribute to Tom Petty", None),
        # A named tribute act before the framing IS the performer.
        ("Damn the Torpedoes: A Tribute to Tom Petty", "Damn the Torpedoes"),
        ("Petty Fest - A Tribute to Tom Petty", "Petty Fest"),
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
        # Gracie Abrams is not performing at her own listening party.
        "(Record Shop) Gracie Abrams Listening Party",
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
