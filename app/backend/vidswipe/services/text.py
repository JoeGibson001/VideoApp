"""Name normalisation helpers shared by tags, search and duplicate detection."""
import re
import unicodedata

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_tag(name: str) -> str:
    """Tag storage form: trimmed, lowercased, internal whitespace collapsed (DESIGN.md 3)."""
    return _WS.sub(" ", (name or "").strip()).lower()


def fold(text: str) -> str:
    """Lowercase and strip accents."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def compact(text: str) -> str:
    """Search form: accent-folded, lowercased, punctuation/space/hyphen/underscore removed."""
    return _NON_ALNUM.sub("", fold(text))


def words(text: str) -> list[str]:
    """Compact words of a name, split on any non-alphanumeric character."""
    return [w for w in _NON_ALNUM.split(fold(text)) if w]


def singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word
