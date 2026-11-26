"""Source URL templates, per-source tag URLs and App-side watch links

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sources") as b:
        b.add_column(sa.Column("creator_url_template", sa.String(length=1024), nullable=True))
        b.add_column(sa.Column("tag_url_template", sa.String(length=1024), nullable=True))
    op.create_table(
        "source_tags",
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
    )
    op.create_table(
        "watch_links",
        sa.Column("watch_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_watch_links_creator_id", "watch_links", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_watch_links_creator_id", table_name="watch_links")
    op.drop_table("watch_links")
    op.drop_table("source_tags")
    with op.batch_alter_table("sources") as b:
        b.drop_column("tag_url_template")
        b.drop_column("creator_url_template")
