"""RAG retriever — keyword-based document retrieval from data/rag/documents/.

Current phase: TF-IDF keyword scoring (no embeddings, no vector store).
Upgrade path: replace retrieve_context() with embedding-based retrieval
on pgvector or ChromaDB when the LLM provider (Bedrock) is available.
Public interface (retrieve_context) remains stable across upgrades.
"""
import math
import re
from pathlib import Path

_DOCS_DIR = Path(__file__).parent.parent / "data" / "rag" / "documents"
_CHUNK_SIZE = 800       # characters per chunk
_CHUNK_OVERLAP = 150    # overlap between consecutive chunks
_TOP_K = 4              # chunks to return


def _load_documents() -> list[dict]:
    docs = []
    for path in sorted(_DOCS_DIR.glob("*.md")):
        try:
            docs.append({"filename": path.name, "content": path.read_text(encoding="utf-8")})
        except OSError:
            pass
    return docs


def _chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + _CHUNK_SIZE])
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())


def _score(query_tokens: list[str], chunk_tokens: list[str],
           doc_freq: dict[str, int], n_docs: int) -> float:
    if not chunk_tokens:
        return 0.0
    n = len(chunk_tokens)
    freq: dict[str, int] = {}
    for t in chunk_tokens:
        freq[t] = freq.get(t, 0) + 1
    return sum(
        (freq.get(t, 0) / n) * (math.log((n_docs + 1) / (doc_freq.get(t, 0) + 1)) + 1)
        for t in set(query_tokens)
    )


def retrieve_context(query_terms: list[str], top_k: int = _TOP_K) -> str:
    """Return the top_k most relevant chunks as a formatted string.

    Args:
        query_terms: tickers, themes or keywords to search in documents
        top_k: maximum number of chunks to return

    Returns:
        String ready to inject into an LLM prompt; empty if no documents found.
    """
    docs = _load_documents()
    if not docs:
        return ""

    chunks: list[dict] = []
    for doc in docs:
        for i, text in enumerate(_chunk_text(doc["content"])):
            chunks.append({
                "filename": doc["filename"],
                "idx": i,
                "content": text,
                "tokens": _tokenize(text),
            })

    doc_freq: dict[str, int] = {}
    for c in chunks:
        for t in set(c["tokens"]):
            doc_freq[t] = doc_freq.get(t, 0) + 1

    query_tokens = _tokenize(" ".join(query_terms))
    for c in chunks:
        c["score"] = _score(query_tokens, c["tokens"], doc_freq, len(chunks))

    ranked = sorted(chunks, key=lambda x: x["score"], reverse=True)[:top_k]
    if not ranked or ranked[0]["score"] == 0.0:
        return ""

    parts = []
    for c in ranked:
        label = c["filename"].replace(".md", "").replace("_", " ").title()
        parts.append(f"[Source: {label}]\n{c['content'].strip()}")

    return "\n\n---\n\n".join(parts)
