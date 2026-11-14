"""Turns pages of text into chunks that can be searched and cited.

Rules:
  - A chunk NEVER spans two pages, so its page number is always exact.
  - Numbered clauses ("12.1", "Clause 14.2") start a new block, so they stay whole.
  - Blocks are packed together up to MAX_CHARS.
  - A block that is too big is split on sentence ends, with a small overlap.
"""
import re
from dataclasses import dataclass

MAX_CHARS = 1200   # about 250-300 words. A starting point we can tune with the evaluation set later.
OVERLAP_CHARS = 150

# A line that begins a numbered clause, e.g. "12.1 ...", "14.2: ...", "Clause 20.4 ..."
CLAUSE_START = re.compile(r"^\s*(?:clause\s+)?\d+(?:\.\d+)*[.):]?\s+", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    page: int          # 1-based page number: this is what the citation will show
    chunk_index: int   # position within the whole document, matches the database column
    text: str


def _split_blocks(page_text: str) -> list[str]:
    """Split a page into blocks: paragraphs, and each numbered clause on its own."""
    blocks = []
    for paragraph in re.split(r"\n\s*\n", page_text.strip()):
        current = []
        for line in paragraph.splitlines():
            if CLAUSE_START.match(line) and current:
                blocks.append(" ".join(current).strip())
                current = []
            current.append(line.strip())
        if current:
            blocks.append(" ".join(current).strip())
    return [b for b in blocks if b]

def _overlap_tail(text: str) -> str:
    """The last ~OVERLAP_CHARS characters of text, trimmed to start at a whole word."""
    if len(text) <= OVERLAP_CHARS:
        return text
    tail = text[-OVERLAP_CHARS:]
    first_space = tail.find(" ")
    return tail[first_space + 1:] if first_space != -1 else tail

def _split_oversized(block: str) -> list[str]:
    """Split one very long block on sentence ends, carrying a short word-aligned overlap."""
    pieces, current = [], ""
    for sentence in SENTENCE_END.split(block):
        # A single sentence longer than MAX_CHARS is cut at the last space before the limit.
        while len(sentence) > MAX_CHARS:
            if current:
                pieces.append(current)
                current = ""
            cut = sentence.rfind(" ", 0, MAX_CHARS)
            if cut <= OVERLAP_CHARS:   # no usable space: hard cut. Also guarantees the loop makes progress.
                cut = MAX_CHARS
            pieces.append(sentence[:cut])
            sentence = _overlap_tail(sentence[:cut]) + " " + sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > MAX_CHARS:
            pieces.append(current)
            current = _overlap_tail(current) + " " + sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_document(pages: list[str]) -> list[Chunk]:
    """pages[0] is page 1. Returns chunks in order, with a document-wide chunk_index."""
    chunks: list[Chunk] = []
    for page_number, page_text in enumerate(pages, start=1):
        packed, current = [], ""
        for block in _split_blocks(page_text):
            parts = _split_oversized(block) if len(block) > MAX_CHARS else [block]
            for part in parts:
                if current and len(current) + 1 + len(part) > MAX_CHARS:
                    packed.append(current)
                    current = part
                else:
                    current = f"{current} {part}".strip()
        if current:
            packed.append(current)
        for text in packed:
            chunks.append(Chunk(page=page_number, chunk_index=len(chunks), text=text))
    return chunks