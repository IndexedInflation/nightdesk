# Models and optional connections

## Core stock forecaster

The app trains a fixed ensemble of LightGBM and linear models on locally downloaded stock history. Choose the public-data setup action on first launch. The app reports download and training progress; it stays paused until preparation succeeds.

No trained weights from another installation are included. The local model is `data/models/production.json` inside your Nightdesk data directory. It stores tree and linear parameters as data instead of Python pickle instructions. Do not import a model file from someone you do not trust.

Training is not an improvement to the research recipe and does not re-run candidate selection on the inspected later years. New daily data, changing index membership, missing companies and dependency differences can change a newly trained model's scores.

## Optional headline classification

The core app works without headline classification. News vetoes are experimental and start disabled.

The original experimental reader is private and is not published or downloaded. A generic pretrained sentiment model is not a compatible replacement: sentiment labels do not mean the same thing as the three trading-news labels.

To configure your own reader:

1. Obtain or train a compatible Hugging Face sequence-classification model from a source you trust. It needs local tokenizer files, `config.json` and `model.safetensors`, with exactly `id2label: {"0":"good","1":"bad","2":"nothing"}`.
2. Use a separate Python environment with PyTorch, Transformers and Safetensors installed. Use CPU or supported local hardware; this feature can use considerable memory.
3. In Settings, enter the Python executable and model directory. Nightdesk validates the local model files and declared labels. The reader loads local files with remote code disabled.
4. If desired, enable the experimental news veto separately. Review its recorded effects; it is not proven to help.

Paths are stored in your own settings and are never part of the shared repository. No broker connection is provided.

## Optional video voice

[The video guide](../video/README.md) covers the public Kokoro voice assets. They are separate from stock prediction and headline classification. Missing assets cause a consent prompt instead of searching someone else's computer.
