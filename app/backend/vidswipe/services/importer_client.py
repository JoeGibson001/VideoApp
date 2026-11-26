"""Thin proxy to the Importer API (DESIGN.md 9, 11.8)."""
import time

import httpx
from fastapi import HTTPException

from ..config import get_settings

_watch_cache: dict[tuple[str, str], tuple[float, list]] = {}
WATCH_CACHE_S = 60


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(base_url=s.importer_url, headers={"X-API-Key": s.importer_api_key}, timeout=15.0)


def request(method: str, path: str, allow: tuple[int, ...] = (), **kwargs):
    """Call the Importer. Status codes in `allow` return their JSON body instead of raising."""
    try:
        with _client() as c:
            r = c.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Importer offline: {exc.__class__.__name__}")
    if r.status_code in allow:
        try:
            return r.json()
        except ValueError:
            return {}
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise HTTPException(r.status_code, detail)
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


_all_cache: tuple[float, list] | None = None


def invalidate_watch_cache() -> None:
    global _all_cache
    _watch_cache.clear()
    _all_cache = None


def all_watches() -> list | None:
    """Every watch in the Importer (cached 60 s). None means the importer is unreachable."""
    global _all_cache
    now = time.monotonic()
    if _all_cache and now - _all_cache[0] < WATCH_CACHE_S:
        return _all_cache[1]
    try:
        data = request("GET", "/api/watches")
    except HTTPException:
        return None
    items = data if isinstance(data, list) else (data or {}).get("items", [])
    _all_cache = (now, items)
    return items


def create_watch(payload: dict) -> dict:
    """Create a watch; if the URL is already watched, return that existing watch (resolved by id)."""
    res = request("POST", "/api/watches", allow=(409,), json=payload)
    if res and res.get("id"):
        return res
    wid = (res or {}).get("watch_id")
    if not wid:
        raise HTTPException(409, (res or {}).get("detail") or "Watch already exists")
    return request("GET", f"/api/watches/{wid}")


def watches_for_creator(domain: str, handles: list[str]) -> list | None:
    """Watches linked to any of the creator's handles. None means the importer is unreachable."""
    out: list = []
    seen = set()
    now = time.monotonic()
    for h in handles:
        key = (domain, h)
        cached = _watch_cache.get(key)
        if cached and now - cached[0] < WATCH_CACHE_S:
            items = cached[1]
        else:
            try:
                data = request("GET", "/api/watches", params={"creator_source_domain": domain,
                                                              "creator_handle": h})
            except HTTPException:
                return None
            items = data if isinstance(data, list) else (data or {}).get("items", [])
            _watch_cache[key] = (now, items)
        for w in items:
            if w["id"] not in seen:
                seen.add(w["id"])
                out.append(w)
    return out
