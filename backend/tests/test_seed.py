"""Data-contract tests for the venue seed list.

`venues.city` feeds downstream consumers verbatim (the deprecated and v1 serializers,
the web client's subdomain lock, and the Backend-Service ETL), so every seeded value
must be a real municipality — display groupings like "Chapel Hill-Carrboro" live in
the query/UI layers, never in the column.
"""

from app.seed import VENUES

# The set of municipalities Triangle Shows actually covers. Extending coverage to a
# new town means adding it here deliberately — a typo or grouping label fails fast.
REAL_MUNICIPALITIES = {"Raleigh", "Cary", "Durham", "Chapel Hill", "Carrboro", "Saxapahaw"}


def test_every_seeded_city_is_a_real_municipality():
    for venue in VENUES:
        assert venue["city"] in REAL_MUNICIPALITIES, (
            f"{venue['name']}: {venue['city']!r} is not a real municipality"
        )


def test_venues_straddling_the_chapel_hill_carrboro_line_carry_their_actual_town():
    cities = {v["slug"]: v["city"] for v in VENUES}
    assert cities["the-cave"] == "Chapel Hill"
    assert cities["local-506"] == "Chapel Hill"
    assert cities["cats-cradle"] == "Carrboro"
    assert cities["cats-cradle-back-room"] == "Carrboro"
    # Koka Booth is in Cary, not Raleigh.
    assert cities["koka-booth"] == "Cary"
