"""CEO Query Analyzer — pre-processing and expansion for recall queries.

Fast path: regex-based keyword extraction, CJK tokenization, named entity
detection, sub-intent splitting.  No LLM required, adds ~5ms.

LLM path: generates keyword expansions, synonyms, and sub-queries.
Opt-in only, adds ~2-5s with local LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# CJK + Latin tokenization
# ---------------------------------------------------------------------------

# Matches: dot-prefixed paths (.cursor-plugin), dunder names (__del__),
# alphanumeric tokens, and CJK characters
_WORD_RE = re.compile(r'\.[\w.\-]+|__\w+__|[a-zA-Z0-9_.\-@/]+|[\u4e00-\u9fff\u3400-\u4dbf]+')
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]+')


# No stopword list — keyword discriminativeness is handled by the scoring
# layer. High-frequency words naturally match many records and get low
# RRF/IDF-like scores, while rare terms (project names, IPs, usernames)
# only match a few records and naturally rank higher.

# ---------------------------------------------------------------------------
# Named entity patterns
# ---------------------------------------------------------------------------

_ENTITY_PATTERNS = [
    re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'),  # IP
    re.compile(r'(\w+@[\w.\-]+)'),                               # user@host
    re.compile(r'(~/[^\s,;]+|/(?:home|usr|var|etc|opt|Users)/[^\s,;]+)'),  # path
    re.compile(r'(https?://\S+)'),                               # URL
    re.compile(r'(?:port|端口)\s*:?\s*(\d{2,5})'),              # port
]

# Sub-intent splitting delimiters
_SPLIT_RE = re.compile(r'[,，;；]|\band\b|以及|还有|同时|并且')


@dataclass
class QueryAnalysis:
    """Result of query pre-processing."""

    original_query: str
    keywords: list[str] = field(default_factory=list)
    sub_queries: list[str] = field(default_factory=list)
    named_entities: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)
    language: str = "mixed"  # "zh", "en", "mixed"


def analyze_query_fast(query: str) -> QueryAnalysis:
    """Fast query analysis: tokenize, extract entities, split sub-intents.

    No LLM required. Adds ~1-5ms to recall latency.
    """
    result = QueryAnalysis(original_query=query)

    # 1. Detect language
    has_cjk = bool(_CJK_RE.search(query))
    has_latin = bool(re.search(r'[a-zA-Z]{2,}', query))
    if has_cjk and has_latin:
        result.language = "mixed"
    elif has_cjk:
        result.language = "zh"
    else:
        result.language = "en"

    # 2. Extract named entities (IPs, user@host, paths, URLs)
    for pattern in _ENTITY_PATTERNS:
        for m in pattern.finditer(query):
            val = m.group(1) if m.lastindex else m.group(0)
            if val and val not in result.named_entities:
                result.named_entities.append(val)

    # 3. Tokenize: extract keywords (no stopword filtering)
    tokens = _WORD_RE.findall(query)
    for t in tokens:
        if t not in result.keywords:
            result.keywords.append(t)

    # 4. Generate bigrams from Latin tokens for multi-word phrase matching
    # e.g. ["Decision", "Tree"] → "Decision Tree"
    latin_tokens = [t for t in tokens if re.match(r'[a-zA-Z]', t)]
    for i in range(len(latin_tokens) - 1):
        bigram = f"{latin_tokens[i]} {latin_tokens[i+1]}"
        if bigram not in result.expanded_terms:
            result.expanded_terms.append(bigram)

    # 5. Split sub-intents
    parts = _SPLIT_RE.split(query)
    parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 3]
    if len(parts) > 1:
        result.sub_queries = parts
    else:
        result.sub_queries = [query]

    return result


# ---------------------------------------------------------------------------
# LLM-enhanced query analysis (opt-in)
# ---------------------------------------------------------------------------

_LLM_ANALYZE_PROMPT = """\
Analyze this memory recall query and generate search strategies.

Query: {query}

Return ONLY a JSON object (no other text):
{{
  "keywords": ["exact terms to search for in stored memories"],
  "sub_queries": ["decomposed sub-questions if the query has multiple intents"],
  "synonyms_en": ["English synonyms or translations of key concepts"],
  "synonyms_zh": ["Chinese synonyms or translations of key concepts"]
}}

Rules:
- keywords: include project names, hostnames, usernames, technical terms.
- sub_queries: only split if the query clearly asks multiple things. \
Otherwise return the original query as a single item.
- synonyms: translate key concepts between Chinese and English. \
Include common paraphrases (e.g., "部署" → "deploy", "deployment", "hosted").
"""


def analyze_query_llm(
    query: str,
    llm_complete: Callable[[str], str],
) -> QueryAnalysis:
    """LLM-enhanced query analysis: generates keyword expansions and sub-queries.

    Adds ~2-5s with local LLM. Falls back to fast analysis on failure.
    """
    fast = analyze_query_fast(query)

    try:
        import json
        prompt = _LLM_ANALYZE_PROMPT.format(query=query)
        raw = llm_complete(prompt)

        # Strip markdown fences
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        data = json.loads(text)

        # Merge LLM results with fast analysis
        for kw in data.get("keywords", []):
            if kw and kw not in fast.keywords:
                fast.keywords.append(kw)

        llm_subs = data.get("sub_queries", [])
        if len(llm_subs) > 1:
            fast.sub_queries = llm_subs

        for term in data.get("synonyms_en", []) + data.get("synonyms_zh", []):
            if term and term not in fast.expanded_terms:
                fast.expanded_terms.append(term)

    except Exception:
        pass  # fall back to fast analysis

    return fast
