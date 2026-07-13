"""Alembic migration 0007: support_artists Text -> text[] (lossless array wire).

Role: Schema migration flipping events.support_artists from a comma-joined Text
string to a Postgres text[]. The comma-joined representation was lossy — any support
name containing a comma ("Earth, Wind & Fire") split into fake acts downstream. The
array stores one name per element instead. Upgrade backfills existing rows in place by
comma-splitting the current string (trimming each element, dropping empties); this
recovers exactly what the string held — historical comma-in-name rows were already
lossy, so no new loss. NULL and '' rows become the empty array. The column is made
NOT NULL with a server_default of '{}', matching the ORM (app/models.py).

Downgrade re-joins the array with ', ' back into Text (nullable, no default), the
inverse of the upgrade split for the common case (names without internal commas).

Requires: A live PostgreSQL database reachable via DATABASE_URL. Transactional DDL, so
the whole convert+backfill is atomic within the upgrade's transaction.
"""

from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Convert Text -> text[] with an in-place USING backfill so no data is lost. The
    # transform mirrors manager._split_support (str.split(',') then str.strip on each
    # element) so a backfilled row equals what a fresh scrape would store:
    #   - coalesce(..., '') maps NULL to the empty string,
    #   - regexp_replace(..., '^\s+|\s+$', '', 'g') strips leading/trailing whitespace
    #     from the whole string — ALL whitespace like str.strip, not just the ASCII
    #     space that btrim() removes, so a stray leading tab/newline can't survive into
    #     an element (btrim would have left it, diverging from the runtime split),
    #   - regexp_split_to_array on '\s*,\s*' splits on each comma AND consumes the
    #     whitespace flanking it, so interior elements come out trimmed too,
    #   - array_remove(..., '') drops empty elements left by '', a leading/trailing
    #     comma, or doubled commas.
    # A USING transform cannot contain a subquery (Postgres rejects "array(SELECT …)"
    # here), so this is expressed purely with scalar/array functions. Raw SQL (not
    # op.alter_column's type_/postgresql_using) keeps the transform explicit. Verified
    # against Postgres to match _split_support byte-for-byte across whitespace/comma edges.
    op.execute(
        r"""
        ALTER TABLE events
            ALTER COLUMN support_artists TYPE text[]
            USING array_remove(
                regexp_split_to_array(
                    regexp_replace(coalesce(support_artists, ''), '^\s+|\s+$', '', 'g'),
                    '\s*,\s*'
                ),
                ''
            )
        """
    )
    # Any remaining NULLs (defensive — the USING above already COALESCEs) become '{}',
    # then lock in NOT NULL + default so new inserts without the column get an empty array.
    op.execute("UPDATE events SET support_artists = '{}' WHERE support_artists IS NULL")
    op.execute("ALTER TABLE events ALTER COLUMN support_artists SET DEFAULT '{}'")
    op.execute("ALTER TABLE events ALTER COLUMN support_artists SET NOT NULL")


def downgrade() -> None:
    # Drop the default and NOT NULL, then re-join the array into a comma+space string.
    # An empty array becomes '' (not NULL) under array_to_string; that's an acceptable
    # inverse — the pre-array column was nullable and downstream treated '' and NULL alike.
    op.execute("ALTER TABLE events ALTER COLUMN support_artists DROP DEFAULT")
    op.execute("ALTER TABLE events ALTER COLUMN support_artists DROP NOT NULL")
    op.execute(
        """
        ALTER TABLE events
            ALTER COLUMN support_artists TYPE text
            USING array_to_string(support_artists, ', ')
        """
    )
