"""Replace Volition's keyword sentiment with a fine-tuned small model, and measure it.

Run (GPU machine, from the repo root):
    python research/news_sentiment/finetune.py            # train + evaluate + export
    python research/news_sentiment/finetune.py --eval-only

Scorers compared on the same held-out data:
  lexicon      backend/services/intelligence.py score_sentiment (what production uses)
  finbert      ProsusAI/finbert, a published finance model (trained on FinancialPhraseBank,
               so its FPB score is contaminated; the tweet set is the fair comparison)
  qwen-zero    Qwen2.5-0.5B-Instruct prompted, no training (the "before")
  qwen-lora    the same model with a LoRA fine-tune and a 3-way classification head (the "after")

Data: FinancialPhraseBank (sentences_50agree, analyst-labelled) and
zeroshot/twitter-financial-news-sentiment (headline-style tweets, closer to the live
news feed). Labels are mapped to negative / neutral / positive. FPB is split 80/10/10
with a fixed seed; the tweet set keeps its published train/validation split.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from datasets import concatenate_datasets, load_dataset
from sklearn.metrics import accuracy_score, classification_report, f1_score

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
from services.intelligence import lexicon_sentiment, sentiment_label  # noqa: E402

LABELS = ["negative", "neutral", "positive"]
BASE = "Qwen/Qwen2.5-0.5B-Instruct"
SEED = 7


def load_data():
    import zipfile
    from datasets import ClassLabel, Dataset
    from huggingface_hub import hf_hub_download
    archive = hf_hub_download("takala/financial_phrasebank", "data/FinancialPhraseBank-v1.0.zip", repo_type="dataset")
    with zipfile.ZipFile(archive) as bundle:
        raw = bundle.read("FinancialPhraseBank-v1.0/Sentences_50Agree.txt").decode("latin-1")
    rows = [line.rsplit("@", 1) for line in raw.splitlines() if "@" in line]
    fpb = Dataset.from_dict({"text": [text.strip() for text, _ in rows],
                             "label": [LABELS.index(label.strip()) for _, label in rows]})
    fpb = fpb.cast_column("label", ClassLabel(names=LABELS))
    split = fpb.train_test_split(test_size=0.2, seed=SEED, stratify_by_column="label")
    held = split["test"].train_test_split(test_size=0.5, seed=SEED, stratify_by_column="label")
    tweets = load_dataset("zeroshot/twitter-financial-news-sentiment")
    remap = {0: 0, 1: 2, 2: 1}                       # tweets: 0 bearish, 1 bullish, 2 neutral
    tweets = tweets.map(lambda r: {"label": remap[r["label"]]}).cast_column("label", ClassLabel(names=LABELS))
    train = concatenate_datasets([split["train"], tweets["train"]]).shuffle(seed=SEED)
    return {"train": train, "dev": held["train"], "fpb_test": held["test"], "tweet_test": tweets["validation"]}


def report(name: str, gold: list[int], pred: list[int], seconds: float) -> dict:
    return {
        "scorer": name,
        "accuracy": round(accuracy_score(gold, pred), 4),
        "macro_f1": round(f1_score(gold, pred, average="macro"), 4),
        "ms_per_text": round(1000 * seconds / len(gold), 2),
        "per_class": {k: round(v["f1-score"], 4) for k, v in
                      classification_report(gold, pred, target_names=LABELS, output_dict=True).items() if k in LABELS},
    }


def lexicon(texts: list[str]) -> list[int]:
    return [LABELS.index(sentiment_label(lexicon_sentiment(t))) for t in texts]


@torch.no_grad()
def finbert(texts: list[str]) -> list[int]:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").cuda().eval()
    names = [model.config.id2label[i].lower() for i in range(3)]
    out = []
    for i in range(0, len(texts), 64):
        batch = tok(texts[i:i + 64], padding=True, truncation=True, max_length=128, return_tensors="pt").to("cuda")
        out += [LABELS.index(names[j]) for j in model(**batch).logits.argmax(-1).tolist()]
    return out


@torch.no_grad()
def qwen_zero(texts: list[str]) -> list[int]:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.bfloat16).cuda().eval()
    prompts = [tok.apply_chat_template([
        {"role": "system", "content": "You classify financial news for a trader. Answer with one word: positive, negative or neutral."},
        {"role": "user", "content": t}], tokenize=False, add_generation_prompt=True) for t in texts]
    out = []
    for i in range(0, len(prompts), 64):
        batch = tok(prompts[i:i + 64], return_tensors="pt", padding=True).to("cuda")
        gen = model.generate(**batch, max_new_tokens=3, do_sample=False)
        for answer in tok.batch_decode(gen[:, batch["input_ids"].shape[1]:], skip_special_tokens=True):
            word = answer.strip().lower()
            out.append(next((LABELS.index(l) for l in LABELS if word.startswith(l[:3])), 1))
    return out


def load_classifier(path: str):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(path)
    model = AutoModelForSequenceClassification.from_pretrained(path, num_labels=3, dtype=torch.bfloat16)
    model.config.pad_token_id = tok.pad_token_id
    return tok, model


def train(data) -> Path:
    from peft import LoraConfig, get_peft_model
    from transformers import DataCollatorWithPadding, Trainer, TrainingArguments
    tok, model = load_classifier(BASE)
    model = get_peft_model(model, LoraConfig(
        task_type="SEQ_CLS", r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()
    encode = lambda batch: tok(batch["text"], truncation=True, max_length=128)  # noqa: E731
    train_set = data["train"].map(encode, batched=True)
    dev_set = data["dev"].map(encode, batched=True)

    def metrics(pred):
        guess = pred.predictions.argmax(-1)
        return {"accuracy": accuracy_score(pred.label_ids, guess), "macro_f1": f1_score(pred.label_ids, guess, average="macro")}

    args = TrainingArguments(
        output_dir=str(OUT / "checkpoints"), num_train_epochs=3, learning_rate=2e-4,
        per_device_train_batch_size=16, gradient_accumulation_steps=2, per_device_eval_batch_size=32,
        group_by_length=True, warmup_ratio=0.06,
        lr_scheduler_type="cosine", weight_decay=0.01, bf16=True, eval_strategy="epoch",
        save_strategy="epoch", save_total_limit=1, load_best_model_at_end=True,
        metric_for_best_model="macro_f1", logging_steps=50, report_to=[], seed=SEED)
    Trainer(model=model, args=args, train_dataset=train_set, eval_dataset=dev_set,
            data_collator=DataCollatorWithPadding(tok), compute_metrics=metrics).train()
    del model
    torch.cuda.empty_cache()
    return merge(max((OUT / "checkpoints").glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[1])))


def merge(checkpoint: Path) -> Path:
    """Fold the best LoRA checkpoint into the base weights (on CPU, to keep GPU memory free)."""
    from peft import PeftModel
    tok, base = load_classifier(BASE)
    merged = OUT / "model"
    PeftModel.from_pretrained(base, checkpoint).merge_and_unload().save_pretrained(merged)
    tok.save_pretrained(merged)
    return merged


@torch.no_grad()
def qwen_lora(texts: list[str], path: Path) -> list[int]:
    tok, model = load_classifier(str(path))
    model = model.cuda().eval()
    out = []
    for i in range(0, len(texts), 64):
        batch = tok(texts[i:i + 64], padding=True, truncation=True, max_length=128, return_tensors="pt").to("cuda")
        out += model(**batch).logits.argmax(-1).tolist()
    return out


def qwen_onnx(texts: list[str]) -> list[int]:
    """The exact artifact deployed to the server (4-bit ONNX, one CPU thread, one headline per call)."""
    import onnxruntime
    from tokenizers import Tokenizer
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 1
    session = onnxruntime.InferenceSession(str(OUT / "onnx" / "model.onnx"), options, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(OUT / "onnx" / "tokenizer.json"))
    tokenizer.enable_truncation(128)
    return [int(session.run(None, {"input_ids": np.asarray([tokenizer.encode(t).ids], dtype=np.int64)})[0].argmax())
            for t in texts]


def timed(fn, texts):
    torch.cuda.synchronize() if torch.cuda.is_available() else None
    start = time.perf_counter()
    pred = fn(texts)
    return pred, time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-only", action="store_true")
    parser.add_argument("--merge-from", type=Path, help="merge this LoRA checkpoint, then evaluate")
    args = parser.parse_args()
    torch.manual_seed(SEED)
    data = load_data()
    path = merge(args.merge_from) if args.merge_from else OUT / "model" if args.eval_only else train(data)
    results = {"base_model": BASE, "sizes": {k: len(v) for k, v in data.items()}, "tests": {}}
    scorers = {"lexicon": lexicon, "finbert": finbert, "qwen-zero": qwen_zero,
               "qwen-lora": lambda texts: qwen_lora(texts, path)}
    if (OUT / "onnx" / "model.onnx").exists():
        scorers["qwen-lora-4bit-cpu"] = qwen_onnx
    for test in ("fpb_test", "tweet_test"):
        gold, texts = list(data[test]["label"]), list(data[test]["text"])
        results["tests"][test] = [report(name, gold, *timed(fn, texts)) for name, fn in scorers.items()]
        for row in results["tests"][test]:
            print(test, row["scorer"], row["accuracy"], row["macro_f1"])
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
