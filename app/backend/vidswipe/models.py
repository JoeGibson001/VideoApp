"""ORM models (DESIGN.md section 3). Every user-specific table is keyed by user_id."""
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Which sources the user sees (user_sources holds the list for "only" and "except"):
    #   all    - every source, including ones added later
    #   only   - just the listed sources
    #   except - every source, including ones added later, apart from the listed ones
    source_mode: Mapped[str] = mapped_column(String(8), default="all")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserSource(Base):
    """The user's source list: shown sources for source_mode "only", hidden ones for "except"."""

    __tablename__ = "user_sources"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)


class AppSetting(Base):
    """Small key/value store for app-wide settings (e.g. what visitors who are not logged in can see)."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class UserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of token
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    icon_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # URL templates for sites where yt-dlp gives no URL. Relative paths are resolved against
    # https://<domain>. Placeholders: see services/urls.py.
    creator_url_template: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tag_url_template: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SourceTag(Base):
    """Per-source settings for a tag: a custom URL overriding the source's tag URL template."""

    __tablename__ = "source_tags"
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class WatchLink(Base):
    """Which creator or source tag an Importer watch was created for from the App.

    The Importer only guesses creator links from the URL, so the App keeps its own record.
    """

    __tablename__ = "watch_links"
    watch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # creator | source_tag
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id", ondelete="CASCADE"), nullable=True,
                                                   index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=True)
    tag_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Creator(Base):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("source_id", "handle"), {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    handle: Mapped[str] = mapped_column(String(255))
    profile_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    source: Mapped[Source] = relationship(lazy="joined")


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("source_id", "source_video_id"), {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    info_json_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    creator_origin_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_video_id: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    poster_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=True)
    container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upload_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    creator: Mapped[Creator] = relationship(lazy="joined")
    source: Mapped[Source] = relationship(lazy="joined")


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VideoTag(Base):
    __tablename__ = "video_tags"
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True)
    origin_name: Mapped[str] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(String(32), default="site")  # site | import_request | admin


class TagAlias(Base):
    __tablename__ = "tag_aliases"
    alias: Mapped[str] = mapped_column(String(255), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CreatorAlias(Base):
    __tablename__ = "creator_aliases"
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    handle: Mapped[str] = mapped_column(String(255), primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MergeLog(Base):
    __tablename__ = "merge_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # tag | creator
    from_ids: Mapped[list] = mapped_column(JSON)
    from_names: Mapped[list] = mapped_column(JSON)
    into_id: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    merged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    merged_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TagDuplicateDismissal(Base):
    __tablename__ = "tag_duplicate_dismissals"
    tag_a: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    tag_b: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Rating(Base):
    __tablename__ = "ratings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True, index=True)
    value: Mapped[int] = mapped_column(Integer)
    rated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class _StatsMixin:
    likes: Mapped[int] = mapped_column(Integer, default=0)
    dislikes: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[int] = mapped_column(Integer, default=0)
    last_rated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserTagStats(_StatsMixin, Base):
    __tablename__ = "user_tag_stats"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class UserCreatorStats(_StatsMixin, Base):
    __tablename__ = "user_creator_stats"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id", ondelete="CASCADE"), primary_key=True)


class UserSourceStats(_StatsMixin, Base):
    __tablename__ = "user_source_stats"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CategoryItem(Base):
    __tablename__ = "category_items"
    __table_args__ = (UniqueConstraint("category_id", "item_type", "item_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), index=True)
    item_type: Mapped[str] = mapped_column(String(16))  # tag | creator | source
    item_id: Mapped[int] = mapped_column(Integer)


class Collection(Base):
    __tablename__ = "collections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    system_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # favorites | liked
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CollectionVideo(Base):
    __tablename__ = "collection_videos"
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SearchHistory(Base):
    __tablename__ = "search_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(255))
    searched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
