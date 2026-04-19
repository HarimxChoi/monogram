"""Anonymizer for harvested fixtures (Track A safety boundary).

Four layers, run in order:

  Layer 1: known-slug replacement (project-a..z rotation)
  Layer 2: regex scrubbers (URLs, emails, phones, addresses, amounts, dates)
  Layer 3: NER-based person-name detection (spaCy if available;
           regex-only fallback with honest coverage caveat)
  Layer 4: residual-PII guard — if anything still matches strict PII regex,
           raise ResidualPII and caller skips the row.

See MONOGRAM_EVAL_PLAN.md §7 for the design rationale.

Dependencies:
    Required: stdlib only (re, unicodedata).
    Optional: spacy + en_core_web_sm + ko NER model. If missing,
              layer 3 degrades to regex-only and logs a warning.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

log = logging.getLogger("monogram.evals.anonymizer")


class ResidualPII(Exception):
    """Raised by scrub() when output still contains PII-shaped strings."""


# ── Regex bank ────────────────────────────────────────────────────────

# URL with optional scheme; preserves arxiv/github/public domains but
# strips query strings unconditionally.
_URL_RE = re.compile(
    r"(?P<scheme>https?://)?(?P<host>[\w.-]+\.[a-z]{2,})(?P<path>/[^\s?#]*)?(?P<qs>\?[^\s#]*)?",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE)

# E.164 + common Korean formats (010-1234-5678, 02-1234-5678)
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}"
)

# Common API-key shapes — the strict guard for layer 4.
_API_KEY_RES = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),        # OpenAI-style
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),          # GitHub fine-grained
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{70,}\b"), # GitHub fine-grained new
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),        # Google API
    re.compile(r"\bxox[bpoa]-[A-Za-z0-9-]{20,}\b"),  # Slack
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),    # Anthropic
]

# Identifying financial amounts (precise cents / exact digits).
# Preserves "10k", "$100", "~$500" (round numbers) as non-identifying.
_FIN_RE = re.compile(
    r"(?:[\$₩¥€£]|\bKRW\s|\bUSD\s|\bEUR\s)[\s]*\d{1,3}(?:,\d{3})+(?:\.\d+)?",
)

# ISO date
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Domains that are safe to keep (public knowledge, no identifying value)
_PUBLIC_DOMAINS = {
    "arxiv.org", "github.com", "wikipedia.org", "youtube.com", "youtu.be",
    "google.com", "scholar.google.com", "openai.com", "anthropic.com",
    "news.ycombinator.com", "reddit.com", "stackoverflow.com",
    "semanticscholar.org", "papers.nips.cc", "openreview.net",
}


# ── Known-slug table ──────────────────────────────────────────────────

@dataclass
class SlugRotation:
    """Consistent-rotation table: first real slug seen → project-a, …"""
    real_to_fake: dict[str, str] = field(default_factory=dict)
    _pool: list[str] = field(
        default_factory=lambda: [f"project-{c}" for c in "abcdefghijklmnop"]
    )

    def replace(self, real_slug: str) -> str:
        if real_slug not in self.real_to_fake:
            if not self._pool:
                # >16 projects — fallback to project-X-N format
                n = len(self.real_to_fake) - 15
                self.real_to_fake[real_slug] = f"project-x-{n}"
            else:
                self.real_to_fake[real_slug] = self._pool.pop(0)
        return self.real_to_fake[real_slug]


# ── spaCy loading (optional) ──────────────────────────────────────────

_SPACY_LOADED = False
_NLP_EN = None
_NLP_KO = None


def _try_load_spacy() -> None:
    global _SPACY_LOADED, _NLP_EN, _NLP_KO
    if _SPACY_LOADED:
        return
    _SPACY_LOADED = True
    try:
        import spacy
    except ImportError:
        log.warning(
            "anonymizer: spacy not installed — Layer 3 (NER) disabled. "
            "Names may leak. Install via `pip install -e '.[eval-nlp]'`."
        )
        return
    try:
        _NLP_EN = spacy.load("en_core_web_sm")
    except Exception as e:
        log.warning("anonymizer: en_core_web_sm load failed: %s", e)
    try:
        _NLP_KO = spacy.load("ko_core_news_sm")
    except Exception as e:
        log.warning("anonymizer: ko_core_news_sm load failed: %s", e)


# ── Layer functions ───────────────────────────────────────────────────

def _layer1_slugs(text: str, known_slugs: list[str], rotation: SlugRotation) -> str:
    """Replace real project slugs with generic counterparts."""
    out = text
    for slug in known_slugs:
        if not slug:
            continue
        # Word-boundary match (kebab-case). Case-insensitive.
        pattern = re.compile(rf"\b{re.escape(slug)}\b", re.IGNORECASE)
        out = pattern.sub(rotation.replace(slug), out)
    return out


def _layer2_regex(text: str) -> str:
    """Scrub URLs, emails, phones, financial amounts, dates."""

    def _url_sub(m: re.Match) -> str:
        host = (m.group("host") or "").lower()
        if host in _PUBLIC_DOMAINS:
            # Preserve the host; strip query string; path preserved
            scheme = m.group("scheme") or "https://"
            return f"{scheme}{host}{m.group('path') or ''}"
        return "https://example.com/X"

    out = _URL_RE.sub(_url_sub, text)
    out = _EMAIL_RE.sub("user@example.com", out)
    out = _PHONE_RE.sub("+00-0000-0000", out)
    out = _FIN_RE.sub("[AMOUNT]", out)
    out = _ISO_DATE_RE.sub("[DATE]", out)
    return out


_SAFE_NAMES = [
    "Alice", "Bob", "Carol", "Dave", "Eve", "Frank",
    "Grace", "Heidi", "Ivan", "Judy", "Mallory", "Olivia",
]


def _layer3_names(text: str, rotation_names: dict[str, str]) -> str:
    """Replace PERSON entities with safe names.

    If spaCy is unavailable, this is a no-op — layer 4 will catch
    obvious name-shaped strings that slipped through.
    """
    _try_load_spacy()
    if _NLP_EN is None and _NLP_KO is None:
        return text

    out = text
    for nlp in (_NLP_EN, _NLP_KO):
        if nlp is None:
            continue
        doc = nlp(out)
        # Replace from right to left so spans stay valid.
        spans = [(ent.start_char, ent.end_char, ent.text) for ent in doc.ents if ent.label_ == "PERSON"]
        for start, end, real in sorted(spans, key=lambda s: -s[0]):
            if real not in rotation_names:
                idx = len(rotation_names) % len(_SAFE_NAMES)
                rotation_names[real] = _SAFE_NAMES[idx]
            out = out[:start] + rotation_names[real] + out[end:]
    return out


def _layer4_residual_check(text: str) -> list[str]:
    """Return list of matched PII-shaped strings. Empty = clean."""
    hits: list[str] = []
    for rx in _API_KEY_RES:
        hits.extend(rx.findall(text))
    # Emails that slipped through layer 2 (rare, but e.g. with unusual TLDs)
    hits.extend(_EMAIL_RE.findall(text))
    return hits


# ── Public API ────────────────────────────────────────────────────────

@dataclass
class AnonymizeResult:
    output: str
    slug_map: dict[str, str]
    name_map: dict[str, str]
    similarity: float  # SequenceMatcher ratio vs original


def scrub(
    text: str,
    known_slugs: list[str] | None = None,
    raise_on_residual: bool = True,
) -> AnonymizeResult:
    """Anonymize drop text. Raises ResidualPII if layer 4 finds leaks.

    `known_slugs` is the list of real project slugs from mono/projects/*.md
    (or mono/config.md if you maintain a manifest). Harvest pipeline
    assembles this list at runtime.
    """
    known_slugs = known_slugs or []
    rotation = SlugRotation()
    name_map: dict[str, str] = {}

    layer1 = _layer1_slugs(text, known_slugs, rotation)
    layer2 = _layer2_regex(layer1)
    layer3 = _layer3_names(layer2, name_map)

    residual = _layer4_residual_check(layer3)
    if residual and raise_on_residual:
        raise ResidualPII(
            f"Residual PII detected: {residual[:5]}{'...' if len(residual) > 5 else ''}"
        )

    similarity = SequenceMatcher(None, text, layer3).ratio()
    return AnonymizeResult(
        output=layer3,
        slug_map=dict(rotation.real_to_fake),
        name_map=name_map,
        similarity=similarity,
    )


def structural_similarity(a: str, b: str) -> float:
    """Character-level similarity 0..1. Used by adversarial tests."""
    return SequenceMatcher(None, a, b).ratio()
