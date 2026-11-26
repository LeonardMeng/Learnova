"""Find the Notion block an issue's quote comes from, and build links to it. Pure: no Notion,
no database.

Quotes come from the cached Markdown (which may carry **bold**, `code` or [link](url) marks),
blocks from Notion's plain text, so both are normalized first. Then:

1. the first block (in reading order) whose text contains the quote; a quote that runs across
   several blocks belongs to the block it starts in;
2. otherwise the block most similar to the quote, if at least `threshold` similar
   (default 0.6, `[issues] anchor_threshold` in config.toml); ties go to the earlier block;
3. otherwise no block: the issue links to the page.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

from learnova.models import BlockText

DEFAULT_THRESHOLD = 0.6

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKS = re.compile(r"[*`~]+|(?<!\w)_+|_+(?!\w)")
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", " ": " "})


def normalize(text: str) -> str:
    text = _LINK.sub(r"\1", text.translate(_QUOTES))
    text = _MARKS.sub("", text)
    return " ".join(text.casefold().split())


def _similarity(quote: str, text: str) -> float:
    """How well the quote matches the best stretch of `text` of the quote's length."""
    if not quote or not text:
        return 0.0
    if len(text) <= len(quote):
        return SequenceMatcher(None, quote, text, autojunk=False).ratio()
    step = max(1, len(quote) // 4)
    best = 0.0
    for start in range(0, len(text) - len(quote) + step, step):
        window = text[start : start + len(quote)]
        best = max(best, SequenceMatcher(None, quote, window, autojunk=False).ratio())
        if best == 1.0:
            break
    return best


def find_block(
    quote: str, blocks: Sequence[BlockText], threshold: float = DEFAULT_THRESHOLD
) -> str | None:
    q = normalize(quote)
    if not q or not blocks:
        return None
    texts = [normalize(b.text) for b in blocks]

    # 1. Contained in one block, or starting in one and running into the next ones.
    for block, text in zip(blocks, texts, strict=True):
        if q in text:
            return block.block_id
    joined, starts = "", []
    for text in texts:
        starts.append(len(joined))
        joined += text + " "
    at = joined.find(q)
    if at >= 0:
        index = max(i for i, s in enumerate(starts) if s <= at)
        return blocks[index].block_id

    # 2. The most similar block, if similar enough.
    best_id, best = None, 0.0
    for block, text in zip(blocks, texts, strict=True):
        score = _similarity(q, text)
        if score > best:
            best_id, best = block.block_id, score
    return best_id if best >= threshold else None


def notion_url(page_id: str, block_id: str | None = None) -> str:
    """https://www.notion.so/<page>#<block>, IDs without dashes; the page alone without a block."""
    url = f"https://www.notion.so/{page_id.replace('-', '')}"
    return f"{url}#{block_id.replace('-', '')}" if block_id else url
