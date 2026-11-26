"""URL templates for creator profiles and tag pages on a source.

A template is either a full URL (https://www.example.com/users/{handle}) or a path that is resolved
against https://<source domain> (/users/{handle}).

Creator placeholders:
  {handle}       handle as stored (URL-encoded, "@" kept), e.g. @bob
  {handle_bare}  handle without a leading "@", e.g. bob
  {name}         display name, URL-encoded
Tag placeholders:
  {tag}          tag name, URL-encoded (spaces as %20)
  {tag_plus}     tag name with spaces as "+" (query-string style)
  {tag_dash}     tag name with spaces as "-" (slug style)
"""
import re
from urllib.parse import quote, quote_plus, urlparse

from fastapi import HTTPException

CREATOR_KEYS = ("handle", "handle_bare", "name")
TAG_KEYS = ("tag", "tag_plus", "tag_dash")


def render(template: str, domain: str, values: dict[str, str]) -> str:
    t = template.strip()
    for k, v in values.items():
        t = t.replace("{" + k + "}", v)
    if not re.match(r"^https?://", t, re.I):
        t = f"https://{domain}" + ("" if t.startswith("/") else "/") + t
    return t


def creator_values(handle: str, name: str | None) -> dict[str, str]:
    handle = (handle or "").strip()
    return {
        "handle": quote(handle, safe="@._-~"),
        "handle_bare": quote(handle.lstrip("@"), safe="._-~"),
        "name": quote((name or handle).strip(), safe="._-~"),
    }


def tag_values(tag: str) -> dict[str, str]:
    return {
        "tag": quote(tag, safe=""),
        "tag_plus": quote_plus(tag),
        "tag_dash": quote(re.sub(r"\s+", "-", tag.strip()), safe="-"),
    }


def creator_url(template: str | None, domain: str, handle: str, name: str | None) -> str | None:
    if not template:
        return None
    return render(template, domain, creator_values(handle, name))


def tag_url(template: str | None, domain: str, tag: str) -> str | None:
    if not template:
        return None
    return render(template, domain, tag_values(tag))


def validate_template(template: str | None, keys: tuple[str, ...], domain: str, what: str) -> str | None:
    """Normalise an admin-entered template; None/blank clears it. Raises 400 when it can't work."""
    if template is None or not template.strip():
        return None
    t = template.strip()
    if not any("{" + k + "}" in t for k in keys):
        raise HTTPException(400, f"The {what} URL template must contain one of: "
                                 + ", ".join("{" + k + "}" for k in keys))
    unknown = set(re.findall(r"\{([^{}]*)\}", t)) - set(keys)
    if unknown:
        raise HTTPException(400, "Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in sorted(unknown)))
    sample = render(t, domain, {k: "x" for k in keys})
    p = urlparse(sample)
    if p.scheme not in ("http", "https") or not p.netloc or " " in sample:
        raise HTTPException(400, f"The {what} URL template does not produce a valid URL")
    return t


def validate_url(url: str | None) -> str | None:
    if url is None or not url.strip():
        return None
    u = url.strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc or " " in u:
        raise HTTPException(400, "Enter a full http(s) URL")
    return u
