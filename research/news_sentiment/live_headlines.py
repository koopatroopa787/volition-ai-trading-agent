"""How the fine-tuned model changes sentiment on Volition's own archived news.

Run inside the backend image with the data volume and model mounted, e.g.
    python /r/live_headlines.py /app/backend/data/archive /m
There are no human labels for these headlines; this measures disagreement and
whether it would have crossed the router's sentiment thresholds, not accuracy.
"""

from __future__ import annotations

import glob
import json
import lzma
import sys
from pathlib import Path

import numpy as np
import onnxruntime
from tokenizers import Tokenizer

sys.path.insert(0, "/app/backend")
from services import intelligence  # noqa: E402

_news_rows = intelligence._news_rows
# Before this change score_sentiment *was* the lexicon; afterwards it is lexicon_sentiment.
lexicon_sentiment = getattr(intelligence, "lexicon_sentiment", intelligence.score_sentiment)

archive, model_dir = Path(sys.argv[1]), Path(sys.argv[2])
options = onnxruntime.SessionOptions()
options.intra_op_num_threads = 1
session = onnxruntime.InferenceSession(str(model_dir / "model.onnx"), options, providers=["CPUExecutionProvider"])
tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
tokenizer.enable_truncation(128)
cache: dict[str, float] = {}


def model_score(text: str) -> float:
    if text not in cache:
        logits = session.run(None, {"input_ids": np.asarray([tokenizer.encode(text).ids], dtype=np.int64)})[0][0]
        p = np.exp(logits - logits.max())
        p /= p.sum()
        cache[text] = float(p[2] - p[0])
    return cache[text]


def gates(score: float) -> tuple[bool, bool, bool]:
    # strategy.py: bullish needs > -0.45, bearish needs < 0.45, condor needs |s| < 0.50
    return score > -0.45, score < 0.45, abs(score) < 0.50


events = flips = 0
lexicon_zero = headlines = 0
examples = []
for path in sorted(glob.glob(str(archive / "*" / "audit.jsonl.xz"))):
    for line in lzma.open(path, "rt", encoding="utf-8"):
        event = json.loads(line)
        if event["kind"] != "market_source_data":
            continue
        rows = _news_rows(event["payload"].get("news"))
        texts = [f"{r.get('headline') or r.get('title') or ''} {r.get('summary') or r.get('description') or ''}".strip()
                 for r in rows]
        texts = [t for t in texts if t]
        if not texts:
            continue
        events += 1
        lex = [lexicon_sentiment(t) for t in texts]
        mod = [model_score(t) for t in texts]
        headlines += len(texts)
        lexicon_zero += sum(v == 0.0 for v in lex)
        old, new = float(np.mean(lex)), float(np.mean(mod))
        if gates(old) != gates(new):
            flips += 1
        for text, a, b in zip(texts, lex, mod):
            if len(examples) < 12 and abs(a - b) > 0.8:
                examples.append({"headline": text[:140], "lexicon": round(a, 2), "model": round(b, 2)})

print(json.dumps({
    "symbol_snapshots_with_news": events, "headlines_scored": headlines, "unique_headlines": len(cache),
    "lexicon_scored_zero_share": round(lexicon_zero / max(1, headlines), 3),
    "snapshots_where_a_router_sentiment_gate_flips": flips,
    "flip_share": round(flips / max(1, events), 3),
    "largest_disagreements": examples,
}, indent=2))
