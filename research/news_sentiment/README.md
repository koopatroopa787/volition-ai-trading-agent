# Replacing a keyword lexicon with a fine-tuned 0.5B model for news sentiment

Volition turns each symbol's recent headlines into a `news_sentiment` score in [-1, 1].
The router reads that score directly: a bullish spread needs it above -0.45, a bearish
spread below +0.45, and an iron condor needs |score| < 0.50. Until this change the score
came from about 40 hand-picked words (`lexicon_sentiment` in
`backend/services/intelligence.py`).

This experiment asks whether a small fine-tuned model can do that job better, cheaply
enough to run on the 2-core ARM server that hosts the bot.

## Data

| Set | Source | Size | Use |
|---|---|---|---|
| FinancialPhraseBank, `Sentences_50Agree` | Malo et al. (2014), analyst-labelled sentences | 4,846 | 80% train / 10% dev / 10% test, stratified, seed 7 |
| Twitter financial news sentiment | `zeroshot/twitter-financial-news-sentiment` | 9,543 train / 2,388 validation | train / test (published split) |

Labels are mapped to negative / neutral / positive. The tweet set is the closest to the
bot's live feed: short headlines with tickers and links.

## Method

* **Base model:** Qwen2.5-0.5B-Instruct (the same family as the Qwen 3B the committee already uses).
* **Fine-tune:** LoRA (r=16, alpha=32) on every attention and MLP projection, plus a new
  3-way classification head. About 8.8M of 503M parameters are trainable.
  3 epochs, learning rate 2e-4 with cosine decay, effective batch 32, bf16.
  Took 5.5 minutes on one RTX 5070 Ti laptop GPU. The dev macro-F1 picks the checkpoint.
* **Deployment form:** merged weights → ONNX → 4-bit weight-only quantization
  (`MatMulNBits`, block 32, embeddings included, int8 compute) → 298 MB.
* **Comparisons:**
  * the production lexicon;
  * FinBERT (`ProsusAI/finbert`), a published finance model;
  * the same Qwen 0.5B prompted with no training ("before");
  * the fine-tuned model on GPU;
  * the exact 4-bit file that runs on the server.

`finetune.py` reproduces every number below, and `export_onnx.py` builds and checks the
deployed file.

## Results

Held-out test sets. Macro-F1 is the main metric because about 60% of sentences are neutral.

| Scorer | Tweets: accuracy | Tweets: macro-F1 | FPB: accuracy | FPB: macro-F1 |
|---|---|---|---|---|
| Lexicon (production before) | 0.699 | 0.465 | 0.590 | 0.252 |
| FinBERT | 0.725 | 0.668 | 0.880 † | 0.873 † |
| Qwen 0.5B, prompted, no training | 0.619 | 0.578 | 0.625 | 0.573 |
| Qwen 0.5B + LoRA (GPU, bf16) | **0.899** | **0.869** | **0.891** | **0.888** |
| Qwen 0.5B + LoRA, 4-bit ONNX on CPU (deployed) | 0.897 | 0.868 | 0.880 | 0.877 |

† FinBERT was trained on FinancialPhraseBank, so its FPB score is not a held-out result.

**Quantization.** All scores below are on the first 500 tweets.

| Format | Accuracy | Size |
|---|---|---|
| fp32 | 0.904 | 1,980 MB |
| Dynamic int8 | 0.832 | 499 MB |
| 4-bit weight-only (deployed) | **0.902** | 298 MB |

Dynamic int8 also quantizes the activations, and small LLMs have outlier activations, which
is the likely reason it lost 7 points. The 4-bit weight-only file agrees with fp32 on 98.6%
of headlines.

**Cost on the server** (Oracle Ampere, aarch64):
* 94 ms per headline on one thread (71 ms on two);
* 1.2 s to load;
* about 1 GB peak RSS.

A 15-minute cycle scores about 50 headlines, which adds about 5 seconds.

## Limits

* **Accuracy is not edge.** Better sentiment changes which structures the router allows, but
  the replay study (`backend/tools/replay_router.py`, `exit_study.py`) found that the
  trend-following entries themselves lost money over Oct–Nov 2026. This change fixes a
  measurement; it does not create a profitable strategy.
* **Neither test set is fully out-of-domain.** Both test sets come from the same sources as
  the training data. The bot's own Alpaca/Benzinga headlines have no labels;
  `live_headlines.py` measures how often the new score would have crossed a router threshold
  on archived snapshots, which is impact, not accuracy.
* **One seed.** The results come from a single training run. Variation across seeds was not
  measured.

## Reproduce

```bash
# GPU machine, from the repo root (Python 3.12, torch with CUDA, transformers, peft, datasets, onnxruntime, onnxscript)
python research/news_sentiment/finetune.py      # train, evaluate, write results.json
python research/news_sentiment/export_onnx.py   # export fp32 ONNX
python research/news_sentiment/export_onnx.py   # quantize + check, write onnx/
# deploy: copy onnx/{model.onnx,tokenizer.json,manifest.json} to the backend volume at data/models/news-sentiment/
```

If the model folder is absent, or onnxruntime fails, the backend falls back to the lexicon,
and `/health` reports `sentiment_scorer`.
