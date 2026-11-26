"""Watch status and toggling for creators and source tags (DESIGN.md 11.8).

A watch belongs to a creator when either the Importer linked it (by source domain + handle from the
listing) or the App recorded it in watch_links when it was created from the creator page. Tag watches
are only known through watch_links.
"""
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Creator, CreatorAlias, Source, SourceTag, Tag, WatchLink
from . import importer_client
from .urls import creator_url, tag_url


def _handle_key(h: str | None) -> str:
    return (h or "").strip().lstrip("@").lower()


def summarize(watches: list[dict] | None) -> dict:
    if watches is None:
        return {"state": "offline", "watches": []}
    if not watches:
        state = "none"
    elif any(w.get("enabled") and not w.get("erroring") for w in watches):
        state = "watching"
    elif any(w.get("erroring") for w in watches):
        state = "error"
    else:
        state = "paused"
    return {"state": state, "watches": watches}


def _linked(db: Session, links: list[WatchLink], all_w: list[dict]) -> list[dict]:
    by_id = {w["id"]: w for w in all_w}
    gone = [l.watch_id for l in links if l.watch_id not in by_id]
    if gone:  # watch was deleted in the Importer: forget the link
        db.execute(delete(WatchLink).where(WatchLink.watch_id.in_(gone)))
        db.commit()
    return [by_id[l.watch_id] for l in links if l.watch_id in by_id]


def creator_watches(db: Session, c: Creator) -> list[dict] | None:
    all_w = importer_client.all_watches()
    if all_w is None:
        return None
    keys = {_handle_key(c.handle)} | {_handle_key(h) for h in db.scalars(
        select(CreatorAlias.handle).where(CreatorAlias.creator_id == c.id))}
    domain = c.source.domain
    out = [w for w in all_w if w.get("creator_source_domain") == domain and _handle_key(w.get("creator_handle")) in keys]
    links = list(db.scalars(select(WatchLink).where(WatchLink.kind == "creator", WatchLink.creator_id == c.id)))
    seen = {w["id"] for w in out}
    out += [w for w in _linked(db, links, all_w) if w["id"] not in seen]
    return out


def creator_status(db: Session, c: Creator) -> dict:
    return summarize(creator_watches(db, c))


def creator_watch_url(c: Creator) -> str | None:
    return c.profile_url or creator_url(c.source.creator_url_template, c.source.domain, c.handle, c.name)


def source_tag_url(db: Session, source: Source, tag: Tag) -> str | None:
    st = db.get(SourceTag, {"source_id": source.id, "tag_id": tag.id})
    if st and st.url:
        return st.url
    return tag_url(source.tag_url_template, source.domain, tag.name)


def source_tag_watches(db: Session, source_id: int, tag_id: int, all_w: list[dict] | None = None) -> list[dict] | None:
    all_w = importer_client.all_watches() if all_w is None else all_w
    if all_w is None:
        return None
    links = list(db.scalars(select(WatchLink).where(WatchLink.kind == "source_tag", WatchLink.source_id == source_id,
                                                    WatchLink.tag_id == tag_id)))
    return _linked(db, links, all_w)


def toggle(db: Session, watches: list[dict] | None, body, url: str | None, name: str, default_tags: list[str],
           link: dict) -> None:
    """Off->On creates (or resumes paused watches); On->Off pauses. Never deletes a watch."""
    if watches is None:
        raise HTTPException(503, "Importer offline")
    targets = [w for w in watches if body.watch_id is None or w["id"] == body.watch_id]
    if body.enabled:
        paused = [w for w in targets if not w.get("enabled")]
        if paused:
            for w in paused:
                importer_client.request("PATCH", f"/api/watches/{w['id']}",
                                        json={"enabled": True, "resume_mode": body.resume_mode})
        elif not targets:
            if not url:
                raise HTTPException(400, "No URL to watch. Set a profile/tag URL or a URL template on the source.")
            payload = {"url": url, "name": name, "tags": body.tags if body.tags else default_tags,
                       "filters": body.filters, "initial_mode": body.initial_mode, "enabled": True}
            if body.backfill_count:
                payload["backfill_count"] = body.backfill_count
            w = importer_client.create_watch(payload)
            if db.get(WatchLink, w["id"]) is None:
                db.add(WatchLink(watch_id=w["id"], **link))
                db.commit()
            if not w.get("enabled"):
                importer_client.request("PATCH", f"/api/watches/{w['id']}",
                                        json={"enabled": True, "resume_mode": body.resume_mode})
    else:
        for w in targets:
            if w.get("enabled"):
                importer_client.request("PATCH", f"/api/watches/{w['id']}", json={"enabled": False})
    importer_client.invalidate_watch_cache()
