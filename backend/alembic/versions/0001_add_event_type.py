"""Alembic migration 0001: creates the initial database schema.

Role: Root migration (down_revision = None). Creates the venues, events, and
scrape_logs tables that form the full application schema. Replaces the original
add/drop event_type pair — those two migrations cancelled each other out and
have been squashed here.
Requires: A live PostgreSQL database reachable via DATABASE_URL.
"""

from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'venues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('city', sa.String(50), nullable=False),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('size_category', sa.String(20), nullable=False),
        sa.Column('website', sa.String(500), nullable=True),
        sa.Column('ticketmaster_venue_id', sa.String(50), nullable=True),
        sa.Column('scraper_type', sa.String(50), nullable=False),
        sa.Column('scraper_config', sa.JSON(), nullable=True),
        sa.Column('color', sa.String(7), nullable=False, server_default='#6366f1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_venues_slug'),
    )
    op.create_index('ix_venues_slug', 'venues', ['slug'])

    op.create_table(
        'events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(200), nullable=True),
        sa.Column('venue_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(500), nullable=False),
        sa.Column('artist', sa.String(300), nullable=True),
        sa.Column('support_artists', sa.Text(), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('doors_time', sa.Time(), nullable=True),
        sa.Column('show_time', sa.Time(), nullable=True),
        sa.Column('ticket_url', sa.String(1000), nullable=True),
        sa.Column('price_min', sa.Float(), nullable=True),
        sa.Column('price_max', sa.Float(), nullable=True),
        sa.Column('image_url', sa.String(1000), nullable=True),
        sa.Column('genre', sa.String(100), nullable=True),
        sa.Column('subgenre', sa.String(100), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='on_sale'),
        sa.Column('age_restriction', sa.String(50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('source_url', sa.String(1000), nullable=True),
        sa.Column('hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['venue_id'], ['venues.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('hash', name='uq_events_hash'),
    )
    op.create_index('ix_events_venue_id', 'events', ['venue_id'])
    op.create_index('ix_events_date', 'events', ['date'])
    op.create_index('ix_events_hash', 'events', ['hash'])

    op.create_table(
        'scrape_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('venue_id', sa.Integer(), nullable=False),
        sa.Column('scraper_type', sa.String(50), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='running'),
        sa.Column('events_found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('events_created', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('events_updated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['venue_id'], ['venues.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scrape_logs_venue_id', 'scrape_logs', ['venue_id'])


def downgrade() -> None:
    op.drop_index('ix_scrape_logs_venue_id', table_name='scrape_logs')
    op.drop_table('scrape_logs')
    op.drop_index('ix_events_hash', table_name='events')
    op.drop_index('ix_events_date', table_name='events')
    op.drop_index('ix_events_venue_id', table_name='events')
    op.drop_table('events')
    op.drop_index('ix_venues_slug', table_name='venues')
    op.drop_table('venues')
