"""Export the fine-tuned classifier to int8 ONNX for the 2-core ARM server, and check it.

Run after finetune.py:  python research/news_sentiment/export_onnx.py
Writes onnx/{model.onnx, tokenizer.json, manifest.json}. Copy that folder to the
backend data volume at data/models/news-sentiment/.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

import numpy as np
import onnxruntime
import torch
import onnx
from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer
from tokenizers import Tokenizer
from transformers import AutoModelForSequenceClassification

from finetune import LABELS, load_data

HERE = Path(__file__).resolve().parent
MODEL, OUT = HERE / "model", HERE / "onnx"


class Logits(torch.nn.Module):
    """One unpadded headline per call: classify from the last token's hidden state.

    Same maths as Qwen2ForSequenceClassification without padding, but with no
    attention-mask construction for the exporter to constant-fold.
    """

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, input_ids):
        hidden = self.model.model(input_ids=input_ids, use_cache=False).last_hidden_state
        return self.model.score(hidden[:, -1])


def main() -> None:
    OUT.mkdir(exist_ok=True)
    fp32 = OUT / "model.fp32.onnx"
    if not fp32.exists():
        # Export and quantize in separate runs on a 32 GB laptop: rerun this script after export.
        model = AutoModelForSequenceClassification.from_pretrained(MODEL, dtype=torch.float32).eval()
        model.config.use_cache = False
        sample = torch.randint(100, 5000, (1, 12), dtype=torch.int64)
        torch.onnx.export(Logits(model), (sample,), fp32, input_names=["input_ids"], output_names=["logits"],
                          opset_version=18, dynamo=True, external_data=True,
                          dynamic_shapes=({1: torch.export.Dim("tokens", min=2, max=512)},))
        print("exported; run again to quantize")
        return
    # Weight-only 4-bit (MatMulNBits + quantized embedding Gather), int8 compute. On 500 tweets:
    # fp32 0.904, dynamic int8 0.832 (activation outliers), this 0.902 at 298 MB. fp32 stays local (gitignored).
    quantizer = MatMulNBitsQuantizer(onnx.load(fp32), bits=4, block_size=32, is_symmetric=True, accuracy_level=4,
                                     op_types_to_quantize=("MatMul", "Gather"),
                                     quant_axes=(("MatMul", 0), ("Gather", 1)))
    quantizer.process()
    quantizer.model.save_model_to_file(str(OUT / "model.onnx"), use_external_data_format=False)
    shutil.copy(MODEL / "tokenizer.json", OUT / "tokenizer.json")

    # Agreement with the bf16 GPU model and single-thread CPU latency on unseen tweets.
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 1
    session = onnxruntime.InferenceSession(str(OUT / "model.onnx"), options, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(OUT / "tokenizer.json"))
    tokenizer.enable_truncation(128)
    test = load_data()["tweet_test"].select(range(500))
    full = onnxruntime.InferenceSession(str(fp32), providers=["CPUExecutionProvider"])
    predictions, reference, started = [], [], time.perf_counter()
    for text in test["text"]:
        ids = np.asarray([tokenizer.encode(text).ids], dtype=np.int64)
        predictions.append(int(session.run(None, {"input_ids": ids})[0].argmax()))
    latency = (time.perf_counter() - started) / len(predictions) * 1000
    for text in test["text"]:
        ids = np.asarray([tokenizer.encode(text).ids], dtype=np.int64)
        reference.append(int(full.run(None, {"input_ids": ids})[0].argmax()))
    gold = np.asarray(test["label"])
    accuracy = float(np.mean(np.asarray(predictions) == gold))
    fp32_accuracy = float(np.mean(np.asarray(reference) == gold))
    agreement = float(np.mean(np.asarray(predictions) == np.asarray(reference)))
    manifest = {
        "labels": LABELS, "base_model": "Qwen/Qwen2.5-0.5B-Instruct", "quantization": "4-bit weight-only (MatMulNBits, block 32) incl. embeddings; int8 compute",
        "size_bytes": (OUT / "model.onnx").stat().st_size,
        "sha256": hashlib.sha256((OUT / "model.onnx").read_bytes()).hexdigest(),
        "check": {"tweets": len(predictions), "accuracy_quantized": round(accuracy, 4),
                  "accuracy_fp32": round(fp32_accuracy, 4), "quantized_agrees_with_fp32": round(agreement, 4),
                  "ms_per_text_1_thread_x86": round(latency, 1)},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
