"""Unit tests for billing-tail parsing and support-artist merging (issue #41).

Pure-function tests — no database or HTTP client. These cover the two new helpers
that make ``support_artists`` richly derived:

- ``parse_billing(billing) -> (headliner, tail_support_names)`` — the shared grammar
  that ``extract_headliner`` now delegates to. It recovers the support acts that live
  in a billing tail ("King Serpent w/ Booster Club, Field Day") that the old headliner
  cut discarded, while keeping the headliner derivation byte-identical (that invariant
  is guarded by test_headliner.py, which stays untouched).
- ``merge_support(structured, tail, headliner)`` — unions the scraper's structured
  performer list with the parsed tail, casefold-dedupes, and excludes the headliner.

The same conservative bias as headliner extraction applies: "&"/"and"/bare "with" are
never delimiters, and no spelling correction — under-capture beats a fabricated act.
"""

import pytest

from app.scrapers.headliner import extract_headliner, merge_support, parse_billing


# --- parse_billing: tail recovery ---


@pytest.mark.parametrize(
    ("billing", "headliner", "support"),
    [
        # The motivating case: the tail names two openers the headliner cut discards.
        # Commas split WITHIN the tail, so both openers come out.
        ("King Serpent w/ Booster Club, Field Day", "King Serpent", ["Booster Club", "Field Day"]),
        # Interleaved delimiters: the old sequential per-delimiter loop kept only the
        # text before the FIRST delimiter and dropped "C". A leftmost-any-delimiter
        # split keeps every later piece → support is [B, C].
        ("A w/ B // C", "A", ["B", "C"]),
        # "with special guest(s)" and feat./ft. tails yield their support name.
        ("Stereolab with special guest Fievel Is Glauque", "Stereolab", ["Fievel Is Glauque"]),
        ("Kelela ft. Yves Tumor", "Kelela", ["Yves Tumor"]),
        ("Jessica Pratt feat. Ryley Walker", "Jessica Pratt", ["Ryley Walker"]),
        ("Deerhoof featuring Saya Gray", "Deerhoof", ["Saya Gray"]),
        # "//" chain — every act after the headliner is support, left-to-right.
        ("Mdou Moctar // Nilüfer Yanya // Truth Club", "Mdou Moctar", ["Nilüfer Yanya", "Truth Club"]),
        # "w/" with no space after the slash still delimits.
        ("Wednesday w/MJ Lenderman", "Wednesday", ["MJ Lenderman"]),
    ],
)
def test_parse_billing_recovers_tail_support(billing, headliner, support):
    assert parse_billing(billing) == (headliner, support)


# --- parse_billing: never-split acts (as billing OR inside a tail) ---


@pytest.mark.parametrize(
    "name",
    [
        # "&"/"and"/bare "with" are not delimiters — these are single acts, no tail.
        "Andy Frasco & The U.N",
        "Iron and Wine",
        "Elvis Costello with Steve Nieve",
        "Duke Ellington & John Coltrane",
    ],
)
def test_parse_billing_never_splits_non_delimiters(name):
    # Whole name is the headliner; the tail is empty (nothing was delimited).
    assert parse_billing(name) == (name, [])


def test_parse_billing_ampersand_act_survives_inside_a_tail():
    # "&" is not a delimiter inside a tail either: "Andy Frasco & The U.N" is one
    # support act, not two.
    assert parse_billing("Wednesday w/ Andy Frasco & The U.N") == (
        "Wednesday",
        ["Andy Frasco & The U.N"],
    )


# --- parse_billing: "w/" must not fire on "w/o" / "w/out" ---


@pytest.mark.parametrize(
    "name",
    ["Angel w/o Wings", "The Man w/out a Country", "Nothing without You"],
)
def test_parse_billing_w_slash_o_is_not_a_delimiter(name):
    assert parse_billing(name) == (name, [])


# --- parse_billing: support decoupled from a null headliner ---


def test_parse_billing_support_survives_non_performance_null():
    # The listening-party framing nulls the HEADLINER, but the cleanly w/-delimited
    # act is independently billed and performing — the tail still returns.
    assert parse_billing("Cat Power Listening Party w/ DJ X") == (None, ["DJ X"])


def test_parse_billing_support_survives_bare_tribute_null():
    # A bare "Tribute to Y" names nobody performing → headliner None; the w/ act stays.
    assert parse_billing("Tribute to Coltrane w/ Giant Steps") == (None, ["Giant Steps"])


def test_parse_billing_named_tribute_act_is_headliner_with_tail():
    # A named tribute act before the framing is the headliner; a trailing w/ act is
    # still support.
    assert parse_billing("Giant Steps: A Tribute to Coltrane w/ Opener X") == (
        "Giant Steps",
        ["Opener X"],
    )


# --- parse_billing: tail hygiene ---


def test_parse_billing_strips_and_drops_empty_tail_pieces():
    # Whitespace around each tail name is stripped; empties (a trailing comma, a
    # doubled delimiter) are dropped.
    assert parse_billing("Headliner w/  Booster Club ,, Field Day ,") == (
        "Headliner",
        ["Booster Club", "Field Day"],
    )


@pytest.mark.parametrize("billing", [None, "", "   "])
def test_parse_billing_blank_input(billing):
    assert parse_billing(billing) == (None, [])


def test_parse_billing_leading_tag_and_framing_strip_before_split():
    # Leading noise tags and framing are stripped first (same as headliner step 1-2),
    # THEN the core is split — the support tail is recovered from the framed core.
    assert parse_billing("(SOLD OUT) An Evening With: Stereolab w/ Fievel Is Glauque") == (
        "Stereolab",
        ["Fievel Is Glauque"],
    )


def test_parse_billing_no_delimiter_has_empty_tail():
    # A clean single act: headliner is the name, tail is empty.
    assert parse_billing("Juana Molina") == ("Juana Molina", [])


# --- extract_headliner delegates to parse_billing (byte-identical behavior) ---
# test_headliner.py is the exhaustive regression guard; this is a spot-check that
# the [0] projection of parse_billing equals extract_headliner across a few shapes.


@pytest.mark.parametrize(
    "billing",
    [
        "King Serpent w/ Booster Club, Field Day",
        "Cat Power Listening Party w/ DJ X",
        "(SOLD OUT) An Evening With: Jessica Pratt",
        "Giant Steps: A Tribute to John Coltrane",
        "Andy Frasco & The U.N",
        None,
    ],
)
def test_extract_headliner_is_parse_billing_first_element(billing):
    assert extract_headliner(billing) == parse_billing(billing)[0]


# --- merge_support: union, order, casefold dedupe, headliner exclusion ---


def test_merge_support_structured_first_then_tail_left_to_right():
    assert merge_support(["Booster Club"], ["Field Day", "Ruler"], "King Serpent") == [
        "Booster Club",
        "Field Day",
        "Ruler",
    ]


def test_merge_support_casefold_dedupe_structured_wins_tie():
    # Structured "Booster Club" and tail "booster club" are the same act by casefold;
    # the first occurrence (structured rendering) wins, the later dupe is dropped.
    assert merge_support(["Booster Club"], ["booster club", "Field Day"], None) == [
        "Booster Club",
        "Field Day",
    ]


def test_merge_support_excludes_headliner_by_casefold():
    # A tail (or structured) name equal to the headliner by casefold is dropped —
    # the headliner is not its own support act.
    assert merge_support([], ["king serpent", "Booster Club"], "King Serpent") == ["Booster Club"]


def test_merge_support_none_headliner_excludes_nothing():
    assert merge_support(["A"], ["B"], None) == ["A", "B"]


def test_merge_support_atomic_comma_name_is_not_split():
    # A structured name that itself contains a comma stays ONE entry — merge_support
    # never splits on internal commas (that's the whole point of the array wire).
    assert merge_support(["Earth, Wind & Fire"], [], None) == ["Earth, Wind & Fire"]


def test_merge_support_does_not_accent_fold():
    # casefold dedupe ONLY — no accent folding. "Nilüfer Yanya" and an ASCII
    # "Nilufer Yanya" are DISTINCT keys, so both survive (over-merge would silently
    # drop a distinct opener).
    assert merge_support(["Nilüfer Yanya"], ["Nilufer Yanya"], None) == [
        "Nilüfer Yanya",
        "Nilufer Yanya",
    ]


def test_merge_support_empty_inputs_yield_empty_list():
    assert merge_support([], [], "Headliner") == []


def test_merge_support_is_deterministic_and_stable():
    # Identical inputs → byte-identical output (no sorting, stable order) so a
    # re-scrape doesn't spuriously stamp updated_at.
    structured = ["Booster Club", "Field Day"]
    tail = ["field day", "Ruler"]
    first = merge_support(structured, tail, "King Serpent")
    second = merge_support(structured, tail, "King Serpent")
    assert first == second == ["Booster Club", "Field Day", "Ruler"]


def test_merge_support_dedupes_within_structured_list():
    # Two structured entries that are the same act by casefold collapse to the first.
    assert merge_support(["Ruler", "ruler"], [], None) == ["Ruler"]


def test_merge_support_preserves_source_order_not_sorted():
    # The union is stable source order (structured-then-tail), NEVER sorted — a sorted()
    # regression would reorder support_artists on every re-scrape and churn updated_at.
    # These names are in the REVERSE of alphabetical order, so a sort would be visible.
    assert merge_support(["Wednesday"], ["Truth Club"], None) == ["Wednesday", "Truth Club"]


def test_merge_support_drops_blank_names_and_normalizes_whitespace():
    # A scraper can leak a blank ("" for a missing schema.org performer name), a None (an
    # explicit "name": null), or an irregularly-spaced rendering. Blanks and None are
    # dropped (None without crashing casefold; the old comma-join + split path dropped
    # blanks too), and each kept name is whitespace-normalized — outer trimmed, internal
    # runs collapsed — the SAME way the headliner is.
    assert merge_support(["", "  ", None, "Truth Club"], ["Field Day", ""], None) == [
        "Truth Club",
        "Field Day",
    ]


def test_merge_support_whitespace_normalization_enables_dedupe_and_exclusion():
    # A padded/irregular structured rendering must still dedupe against the clean tail
    # rendering, and a headliner with different internal spacing must still be excluded —
    # both rely on the same normalization the manager applies to the headliner.
    assert merge_support([" Truth  Club "], ["Truth Club"], None) == ["Truth Club"]
    assert merge_support(["King  Serpent", "Truth Club"], [], "King Serpent") == ["Truth Club"]
