from app.chunker import chunk_document

pages = [
    # Page 1: two short clauses, which should be packed into one chunk
    "12.1 Brightwater shall deliver all pallets within 48 hours.\n"
    "12.2 Late delivery incurs a penalty of 500 GBP per day.",
    # Page 2: one very long paragraph, which should be split with overlap
    ("This indemnity applies to all losses arising from the services. " * 40).strip(),
    # Page 3: a blank page, which should produce nothing
    "",
]

for c in chunk_document(pages):
    print(f"page {c.page} | index {c.chunk_index} | {len(c.text)} chars | {c.text[:60]!r}")
    