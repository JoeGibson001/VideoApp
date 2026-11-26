"""Initial schema: jobs, watches, watch_items, watch_runs.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("progress", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("detail", sa.Text),
        sa.Column("stderr_tail", sa.Text),
        sa.Column("outcome", sa.String(32)),
        sa.Column("video_id", sa.Integer),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("expand_playlists", sa.Boolean, nullable=False),
        sa.Column("max_items", sa.Integer, nullable=False),
        sa.Column("parent_id", sa.Integer),
        sa.Column("watch_id", sa.Integer),
        sa.Column("watch_item_id", sa.String(255)),
        sa.Column("options", sa.JSON, nullable=False),
        sa.Column("source_domain", sa.String(255)),
        sa.Column("source_video_id", sa.String(255)),
        sa.Column("title", sa.Text),
        sa.Column("payload", sa.JSON),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_watch_id", "jobs", ["watch_id"])

    op.create_table(
        "watches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("normalized_url", sa.Text, nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("source_domain", sa.String(255)),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("paused_at", sa.DateTime),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("filters", sa.JSON, nullable=False),
        sa.Column("initial_mode", sa.String(32), nullable=False),
        sa.Column("backfill_count", sa.Integer, nullable=False),
        sa.Column("initial_done", sa.Boolean, nullable=False),
        sa.Column("rebaseline_pending", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("last_checked_at", sa.DateTime),
        sa.Column("last_success_at", sa.DateTime),
        sa.Column("last_new_count", sa.Integer),
        sa.Column("last_error", sa.Text),
        sa.Column("consecutive_failures", sa.Integer, nullable=False),
        sa.Column("next_run_at", sa.DateTime),
        sa.Column("creator_source_domain", sa.String(255)),
        sa.Column("creator_handle", sa.String(255)),
        sa.Column("avatar_url", sa.Text),
    )
    op.create_index("ix_watches_next_run_at", "watches", ["next_run_at"])

    op.create_table(
        "watch_items",
        sa.Column("watch_id", sa.Integer, sa.ForeignKey("watches.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_video_id", sa.String(255), primary_key=True),
        sa.Column("url", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("first_seen_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("job_id", sa.Integer),
        sa.Column("detail", sa.Text),
        sa.Column("retry_count", sa.Integer, nullable=False),
    )

    op.create_table(
        "watch_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("watch_id", sa.Integer, sa.ForeignKey("watches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("listed_count", sa.Integer, nullable=False),
        sa.Column("new_count", sa.Integer, nullable=False),
        sa.Column("queued_count", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_watch_runs_watch_id", "watch_runs", ["watch_id"])


def downgrade() -> None:
    op.drop_table("watch_runs")
    op.drop_table("watch_items")
    op.drop_table("watches")
    op.drop_table("jobs")
