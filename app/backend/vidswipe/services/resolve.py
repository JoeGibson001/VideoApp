"""Single entry points for names entering the system (DESIGN.md 9.1)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Creator, CreatorAlias, Source, Tag, TagAlias
from . import search_index
from .text import normalize_tag


def find_tag(db: Session, name: str) -> tuple[Tag | None, bool]:
    """Return (tag, via_alias) for a name without creating anything."""
    norm = normalize_tag(name)
    if not norm:
        return None, False
    tag = db.scalar(select(Tag).where(Tag.name == norm))
    if tag:
        return tag, False
    alias = db.get(TagAlias, norm)
    if alias:
        return db.get(Tag, alias.tag_id), True
    return None, False


def resolve_tag(db: Session, name: str) -> Tag | None:
    """Normalise, then exact tag, then alias, else create."""
    norm = normalize_tag(name)
    if not norm:
        return None
    tag, _ = find_tag(db, norm)
    if tag:
        return tag
    tag = Tag(name=norm)
    db.add(tag)
    db.flush()
    search_index.invalidate()
    return tag


def upsert_source(db: Session, domain: str, name: str | None = None) -> Source:
    domain = (domain or "unknown").lower().removeprefix("www.")
    src = db.scalar(select(Source).where(Source.domain == domain))
    if src is None:
        src = Source(domain=domain, name=name or domain)
        db.add(src)
        db.flush()
        search_index.invalidate()
    return src


def find_creator(db: Session, source_id: int, handle: str) -> Creator | None:
    c = db.scalar(select(Creator).where(Creator.source_id == source_id, Creator.handle == handle))
    if c:
        return c
    alias = db.get(CreatorAlias, (source_id, handle))
    if alias:
        return db.get(Creator, alias.creator_id)
    return None


def resolve_creator(db: Session, source: Source, handle: str, name: str | None = None,
                    profile_url: str | None = None, avatar_path: str | None = None) -> Creator:
    handle = (handle or name or "unknown").strip()
    c = find_creator(db, source.id, handle)
    if c is None:
        c = Creator(source_id=source.id, handle=handle, name=name or handle, profile_url=profile_url,
                    avatar_path=avatar_path)
        db.add(c)
        db.flush()
        search_index.invalidate()
    else:
        if avatar_path and not c.avatar_path:
            c.avatar_path = avatar_path
        if profile_url and not c.profile_url:
            c.profile_url = profile_url
    return c
