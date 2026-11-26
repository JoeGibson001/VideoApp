"""Three-way source access: all / only listed / all except listed

Replaces users.all_sources (bool) with users.source_mode.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("source_mode", sa.String(length=8), nullable=False, server_default="all"))
    op.execute("UPDATE users SET source_mode = CASE WHEN all_sources THEN 'all' ELSE 'only' END")
    with op.batch_alter_table("users") as b:
        b.drop_column("all_sources")
    # Visitor setting {"all_sources": bool} becomes {"mode": ...}; the reader also accepts the old shape.
    op.execute("UPDATE app_settings SET value = json_object('mode', CASE WHEN json_extract(value, '$.all_sources') "
               "THEN 'all' ELSE 'only' END, 'source_ids', json(COALESCE(json_extract(value, '$.source_ids'), '[]'))) "
               "WHERE key = 'anonymous_sources' AND json_extract(value, '$.mode') IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("all_sources", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute("UPDATE users SET all_sources = CASE WHEN source_mode = 'all' THEN 1 ELSE 0 END")
    with op.batch_alter_table("users") as b:
        b.drop_column("source_mode")
