"""The language model that writes answers.

A model is anything with a .model_id and a .generate(system, user) -> str method.
  FakeModel          - offline and deterministic: quotes the top passage. For building and testing only.
  BedrockClaudeModel - a Claude model on AWS Bedrock. Written but NOT yet verified against real AWS.

Choose with the ANSWER_MODEL environment variable: "fake" (default) or "bedrock".
"""
import json
import os
import re

# The prompt format is defined in app/answering.py; the fake model reads the same format.
PASSAGE = re.compile(r'<passage id="(\d+)" source="[^"]*">(.*?)</passage>', re.DOTALL)


class FakeModel:
    """Quotes the first sentence of the top-ranked passage. No understanding, just plumbing."""

    model_id = "fake-extractive-v1"

    def generate(self, system: str, user: str) -> str:
        passages = PASSAGE.findall(user)
        if not passages:
            return json.dumps({"answer_found": False, "claims": []})
        passage_id, text = passages[0]
        text = " ".join(text.split())
        first_sentence = re.split(r"(?<=[.!?])\s+", text)[0]
        quote = first_sentence if len(first_sentence.split()) >= 3 else text
        return json.dumps({
            "answer_found": True,
            "claims": [{"text": quote, "source": int(passage_id), "quote": quote}],
        })


class BedrockClaudeModel:
    """A Claude model through the AWS Bedrock Converse API.

    Not verified yet: you must choose the model id yourself (set BEDROCK_MODEL_ID) and confirm in the
    AWS console that you have model access in your region. There is deliberately no default id."""

    def __init__(self, model_id: str | None = None, region: str | None = None):
        import boto3  # imported here so the fake model works without boto3 installed
        self.model_id = model_id or os.environ.get("BEDROCK_MODEL_ID")
        if not self.model_id:
            raise ValueError("set BEDROCK_MODEL_ID to the Bedrock model you have access to")
        self.client = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION", "eu-west-2"))

    def generate(self, system: str, user: str) -> str:
        resp = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": 1000, "temperature": 0},  # temperature 0: as repeatable as possible
        )
        return resp["output"]["message"]["content"][0]["text"]


def get_model():
    kind = os.environ.get("ANSWER_MODEL", "fake").lower()
    if kind == "fake":
        return FakeModel()
    if kind == "bedrock":
        return BedrockClaudeModel()
    raise ValueError(f"unknown ANSWER_MODEL {kind!r} (use 'fake' or 'bedrock')")