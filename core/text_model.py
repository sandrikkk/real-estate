"""Text classification model: character 3-5 gram logistic regression with SGD."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from typing import Any, Dict, List, Optional

NGRAM_SIZES = (3, 4, 5)
MIN_DF = 5  # a feature must appear in at least 5 training texts
MAX_FEATURES = 20000  # vocabulary before training, by document frequency, ties by the string
EPOCHS = 5
LEARNING_RATE = 0.1
L2 = 1e-6
SEED = 42
MAX_JSON_BYTES = 90_000  # D1 statement limit is 100 KB

_DIGIT_RE = re.compile(r"[0-9]")
_CLEAN_RE = re.compile(r"[^ა-ჰa-zа-яё0$]+")


def normalize(text: str) -> str:
    """Normalize text byte-for-byte identically to Worker's textModelNormalize."""
    t = text.lower()
    t = _DIGIT_RE.sub("0", t)
    t = _CLEAN_RE.sub(" ", t)
    return " " + t.strip() + " "


def features(text: str) -> List[str]:
    """Unique character n-grams of lengths 3, 4, 5 sorted alphabetically."""
    t = normalize(text)
    res = set()
    t_len = len(t)
    for n in NGRAM_SIZES:
        for i in range(t_len - n + 1):
            res.add(t[i : i + n])
    return sorted(res)


def predict(model: Dict[str, Any], text: str) -> Dict[str, float]:
    """Predict class probabilities for input text."""
    feats = features(text)
    classes: List[str] = model["classes"]
    n_classes = len(classes)
    bias: List[float] = model.get("bias") or [0.0] * n_classes
    weights: Dict[str, List[float]] = model.get("weights") or {}

    scores = list(bias)
    for f in feats:
        w_f = weights.get(f)
        if w_f is not None:
            for c in range(n_classes):
                scores[c] += w_f[c]

    max_s = max(scores)
    exps = [math.exp(s - max_s) for s in scores]
    sum_exps = sum(exps)
    return {cls: exps[c] / sum_exps for c, cls in enumerate(classes)}


def test_split(row_id: str) -> bool:
    """Deterministic 20% test split based on sha1 hash."""
    return int(hashlib.sha1(str(row_id).encode("utf-8")).hexdigest(), 16) % 5 == 0


def pick_threshold(
    probs: List[float], correct: List[bool], target: float, min_samples: int = 50
) -> Optional[float]:
    """Find the smallest threshold where precision >= target on at least min_samples."""
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99]
    for t in thresholds:
        indices = [i for i, p in enumerate(probs) if p >= t]
        if len(indices) >= min_samples:
            acc = sum(1 for i in indices if correct[i]) / len(indices)
            if acc >= target:
                return t
    return None


def train(texts: List[str], labels: List[str], classes: List[str], kind: str) -> Dict[str, Any]:
    """Train a character n-gram logistic regression model using SGD."""
    # 1. Feature extraction and vocabulary selection
    doc_features: List[List[str]] = [features(t) for t in texts]
    df_counter: Counter = Counter()
    for feats in doc_features:
        for f in feats:
            df_counter[f] += 1

    # Keep df >= MIN_DF, sorted by (-df, feature_string), top MAX_FEATURES
    candidates = [f for f, count in df_counter.items() if count >= MIN_DF]
    candidates.sort(key=lambda f: (-df_counter[f], f))
    vocab = set(candidates[:MAX_FEATURES])

    # Filter doc features to vocabulary
    filtered_doc_features: List[List[str]] = [
        [f for f in feats if f in vocab] for feats in doc_features
    ]

    # 2. Initialize weights and bias
    n_classes = len(classes)
    class_to_idx = {cls: idx for idx, cls in enumerate(classes)}
    w: Dict[str, List[float]] = {f: [0.0] * n_classes for f in vocab}
    bias: List[float] = [0.0] * n_classes

    # Class balancing weights: n_total / (n_classes * n_of_its_class)
    n_total = len(texts)
    class_counts = Counter(labels)
    sample_weights: Dict[str, float] = {}
    for cls in classes:
        cnt = class_counts.get(cls, 0)
        sample_weights[cls] = (n_total / (n_classes * cnt)) if cnt > 0 else 1.0

    # 3. SGD Training
    rng = random.Random(SEED)
    indices = list(range(n_total))

    for _ in range(EPOCHS):
        rng.shuffle(indices)
        for idx in indices:
            label = labels[idx]
            label_idx = class_to_idx[label]
            s_weight = sample_weights[label]
            f_list = filtered_doc_features[idx]

            # Forward pass: score = bias + sum(w)
            scores = [bias[c] + sum(w[f][c] for f in f_list) for c in range(n_classes)]
            max_s = max(scores)
            exps = [math.exp(s - max_s) for s in scores]
            sum_exps = sum(exps)
            p = [e / sum_exps for e in exps]

            # Gradient update
            for c in range(n_classes):
                y_c = 1.0 if c == label_idx else 0.0
                g = (p[c] - y_c) * s_weight
                bias[c] -= LEARNING_RATE * g
                for f in f_list:
                    w[f][c] -= LEARNING_RATE * (g + L2 * w[f][c])

    # 4. Rounding and pruning
    rounded_bias = [round(b, 4) for b in bias]
    rounded_w: Dict[str, List[float]] = {}
    for f in vocab:
        w_f = [round(val, 4) for val in w[f]]
        if any(v != 0.0 for v in w_f):
            rounded_w[f] = w_f

    model: Dict[str, Any] = {
        "kind": kind,
        "version": 1,
        "classes": classes,
        "bias": rounded_bias,
        "weights": rounded_w,
        "threshold": None,
    }

    # Prune 10% smallest max(abs(w)) until under MAX_JSON_BYTES
    while len(json.dumps(model, ensure_ascii=False).encode("utf-8")) > MAX_JSON_BYTES:
        if not model["weights"]:
            break
        sorted_feats = sorted(
            model["weights"].keys(), key=lambda f: (max(abs(x) for x in model["weights"][f]), f)
        )
        n_remove = max(1, len(model["weights"]) // 10)
        for f in sorted_feats[:n_remove]:
            del model["weights"][f]

    return model
