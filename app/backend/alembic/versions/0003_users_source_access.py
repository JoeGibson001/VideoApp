"""Local users: account status and per-user / visitor source access

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        # Existing users keep seeing everything, as before.
        b.add_column(sa.Column("all_sources", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "user_sources",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("user_sources")
    with op.batch_alter_table("users") as b:
        b.drop_column("all_sources")
        b.drop_column("is_active")
