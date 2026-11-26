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


def request(method: str, path: str, **kwargs):
    try:
        with _client() as c:
            r = c.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Importer offline: {exc.__class__.__name__}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise HTTPException(r.status_code, detail)
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def invalidate_watch_cache() -> None:
    _watch_cache.clear()


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
