"""Turns text into vectors (lists of 1024 numbers) so we can search by similarity.

Two interchangeable embedders:
  LocalHashEmbedder    - free, offline, deterministic. Matches on shared WORDS only (no meaning).
  BedrockTitanEmbedder - the real thing: Amazon Titan Text Embeddings V2, 1024 dimensions.

Choose with the EMBEDDER environment variable: "local" (default) or "bedrock".
IMPORTANT: documents and queries must be embedded with the SAME embedder. Vectors from
different models live in different spaces; mixing them returns confident nonsense.
"""
import hashlib
import json
import math
import os
import re

DIM = 1024  # must match vector(1024) in the chunks table
WORD = re.compile(r"[a-z0-9]+")


class LocalHashEmbedder:
    """The 'hashing trick': each word is hashed to one of 1024 slots and nudges it up or down.
    Texts that share words end up pointing in similar directions. It cannot know that
    'terminate' and 'cancel' are related, so it is for building and testing the plumbing only."""

    model_id = "local-hash-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        words = WORD.findall(text.lower())
        if not words:
            raise ValueError("cannot embed empty text")
        vec = [0.0] * DIM
        for word in words:
            h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
            vec[h % DIM] += 1.0 if (h >> 63) & 1 else -1.0   # slot from the low bits, sign from the top bit
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]                        # unit length, so cosine = dot product


class BedrockTitanEmbedder:
    """Amazon Titan Text Embeddings V2 through AWS Bedrock. Needs AWS credentials and model access."""

    model_id = "amazon.titan-embed-text-v2:0"

    def __init__(self, region: str | None = None):
        import boto3  # imported here so the local embedder works without boto3 installed
        self.client = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION", "eu-west-2"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:  # Titan takes one text per call
            resp = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({"inputText": text, "dimensions": DIM, "normalize": True}),
                contentType="application/json",
                accept="application/json",
            )
            vec = json.loads(resp["body"].read())["embedding"]
            if len(vec) != DIM:
                raise RuntimeError(f"expected {DIM} dimensions, got {len(vec)}")
            out.append(vec)
        return out


def get_embedder():
    kind = os.environ.get("EMBEDDER", "local").lower()
    if kind == "local":
        return LocalHashEmbedder()
    if kind == "bedrock":
        return BedrockTitanEmbedder()
    raise ValueError(f"unknown EMBEDDER {kind!r} (use 'local' or 'bedrock')")


def to_pgvector(vec: list[float]) -> str:
    """Format a vector the way Postgres' pgvector type expects it: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
