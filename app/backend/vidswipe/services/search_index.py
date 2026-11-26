"""In-memory fuzzy search over tags, creators and sources (DESIGN.md 6.4).

The searchable set is names only, so it is held in memory and rebuilt lazily after any change.
Per-user collections and categories are small and are scored directly from the database.
"""
import threading
from dataclasses import dataclass, field

from rapidfuzz.distance import DamerauLevenshtein
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Creator, Source, Tag, TagAlias, Video, VideoTag
from .text import compact, fold, words


@dataclass
class Entry:
    type: str
    id: int
    name: str
    primary: list[str]  # names that can be matched as the item's own name
    aliases: list[str] = field(default_factory=list)
    video_count: int = 0
    extra: dict = field(default_factory=dict)
    # precomputed forms
    p_compact: list[str] = field(default_factory=list)
    p_words: list[list[str]] = field(default_factory=list)
    a_compact: list[str] = field(default_factory=list)

    def prepare(self) -> "Entry":
        self.p_compact = [compact(n) for n in self.primary if n]
        self.p_words = [words(n) for n in self.primary if n]
        self.a_compact = [compact(a) for a in self.aliases if a]
        return self


_lock = threading.Lock()
_index: dict[str, list[Entry]] | None = None


def invalidate() -> None:
    global _index
    with _lock:
        _index = None


def _build(db: Session) -> dict[str, list[Entry]]:
    vis = Video.is_hidden.is_(False)
    tag_counts = dict(db.execute(select(VideoTag.tag_id, func.count()).join(Video, Video.id == VideoTag.video_id)
                                 .where(vis).group_by(VideoTag.tag_id)).all())
    creator_counts = dict(db.execute(select(Video.creator_id, func.count()).where(vis).group_by(Video.creator_id)).all())
    source_counts = dict(db.execute(select(Video.source_id, func.count()).where(vis).group_by(Video.source_id)).all())
    aliases: dict[int, list[str]] = {}
    for a in db.scalars(select(TagAlias)):
        aliases.setdefault(a.tag_id, []).append(a.alias)
    idx: dict[str, list[Entry]] = {"tag": [], "creator": [], "source": []}
    for t in db.scalars(select(Tag)):
        idx["tag"].append(Entry("tag", t.id, t.name, [t.name], aliases.get(t.id, []), tag_counts.get(t.id, 0),
                                {"created_at": t.created_at}).prepare())
    for c in db.scalars(select(Creator)):
        idx["creator"].append(Entry("creator", c.id, c.name, [c.name, c.handle], [], creator_counts.get(c.id, 0),
                                    {"handle": c.handle, "avatar_path": c.avatar_path, "source_id": c.source_id,
                                     "created_at": c.created_at}).prepare())
    for s in db.scalars(select(Source)):
        idx["source"].append(Entry("source", s.id, s.name, [s.name, s.domain], [], source_counts.get(s.id, 0),
                                   {"domain": s.domain, "icon_path": s.icon_path,
                                    "created_at": s.created_at}).prepare())
    return idx


def get_index(db: Session) -> dict[str, list[Entry]]:
    global _index
    with _lock:
        if _index is None:
            _index = _build(db)
        return _index


def allowed_typos(qlen: int) -> int:
    if qlen <= 3:
        return 0
    if qlen <= 6:
        return 1
    return 2


def _is_subsequence(q: str, s: str) -> bool:
    it = iter(s)
    return all(ch in it for ch in q)


# Higher is better. Rank 1 in the design table maps to the highest band.
RANK_BANDS = {1: 7000, 2: 6000, 3: 5000, 4: 4000, 5: 3000, 6: 2000, 7: 1000}


def match_score(q: str, primary_compact: list[str], primary_words: list[list[str]],
                alias_compact: list[str]) -> tuple[int, int] | None:
    """Return (rank, score) for the best match of compact query q, or None.

    Within a rank, shorter names that cover more of the query rank higher.
    """
    if not q:
        return None
    best: tuple[int, int] | None = None

    def consider(rank: int, name_len: int, penalty: int = 0) -> None:
        nonlocal best
        coverage = int(1000 * len(q) / max(name_len, len(q)))
        sc = RANK_BANDS[rank] + min(coverage, 999) - penalty
        if best is None or sc > best[1]:
            best = (rank, sc)

    for name in primary_compact:
        if name == q:
            consider(1, len(name))
    for a in alias_compact:
        if a == q:
            consider(2, len(a))
    if best and best[0] <= 2:
        return best
    for name in primary_compact:
        if name.startswith(q):
            consider(3, len(name))
    for ws, name in zip(primary_words, primary_compact):
        if any(w.startswith(q) for w in ws[1:]):
            consider(4, len(name))
    for name in primary_compact:
        if q in name:
            consider(5, len(name))
    if best:
        return best
    k = allowed_typos(len(q))
    if k:
        for ws, name in zip(primary_words, primary_compact):
            cands = [name] + ws + [name[: len(q)]] + [w[: len(q)] for w in ws]
            d = min(DamerauLevenshtein.distance(q, c, score_cutoff=k + 1) for c in cands if c)
            if d <= k:
                consider(6, len(name), penalty=d * 100)
        for a in alias_compact:
            d = DamerauLevenshtein.distance(q, a, score_cutoff=k + 1)
            if d <= k:
                consider(6, len(a), penalty=d * 100 + 50)
        if best:
            return best
    if len(q) >= 2:
        for name in primary_compact:
            if _is_subsequence(q, name):
                consider(7, len(name))
    return best


def search_entries(db: Session, query: str, types: list[str]) -> dict[str, list[tuple[Entry, int, int]]]:
    """Return matched entries per type as (entry, rank, score), unsorted."""
    q = compact(query)
    idx = get_index(db)
    out: dict[str, list[tuple[Entry, int, int]]] = {}
    for t in types:
        if t not in idx:
            continue
        res = []
        for e in idx[t]:
            m = match_score(q, e.p_compact, e.p_words, e.a_compact)
            if m:
                res.append((e, m[0], m[1]))
        out[t] = res
    return out


def highlight_spans(query: str, name: str) -> list[list[int]]:
    """Character ranges [start, end) of name that match the query, for highlighting."""
    q = compact(query)
    if not q:
        return []
    f = fold(name)
    # map compact index -> original index
    positions = [i for i, ch in enumerate(f) if ch.isalnum() and ch.isascii()]
    comp = "".join(f[i] for i in positions)
    start = comp.find(q)
    matched: list[int] = []
    if start >= 0:
        matched = positions[start:start + len(q)]
    else:
        it = 0
        for qi in q:
            while it < len(comp) and comp[it] != qi:
                it += 1
            if it >= len(comp):
                matched = []
                break
            matched.append(positions[it])
            it += 1
    spans: list[list[int]] = []
    for i in matched:
        if spans and spans[-1][1] == i:
            spans[-1][1] = i + 1
        else:
            spans.append([i, i + 1])
    return spans
