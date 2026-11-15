"""Unit tests for core/text_model.py."""

from __future__ import annotations

import unittest

from core.text_model import (
    features,
    normalize,
    pick_threshold,
    predict,
    test_split,
    train,
)


class TestTextModel(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize(""), "  ")
        self.assertEqual(normalize("ტესტი"), " ტესტი ")
        self.assertEqual(normalize("85000$"), " 00000$ ")
        self.assertEqual(normalize("🔥 იყიდება 💥"), " იყიდება ")
        self.assertEqual(normalize("Rent КВАРТИРА"), " rent квартира ")

    def test_features(self):
        feats = features("ტესტი")
        self.assertIn("ტეს", feats)
        self.assertIn(" ტეს", feats)
        self.assertEqual(feats, sorted(set(feats)))

    def test_train_determinism_and_prediction(self):
        texts = []
        labels = []
        for i in range(40):
            texts.append(f"იყიდება {i + 1} ოთახიანი ბინა {50 + i} კვ.მ ფასი {80000 + i * 1000}$")
            labels.append("sale")
            texts.append(
                f"ქირავდება {i + 1} ოთახიანი ბინა {50 + i} კვ.მ ფასი {500 + i * 50}$ თვეში"
            )
            labels.append("rent")

        m1 = train(texts, labels, ["sale", "rent"], "deal_type")
        m2 = train(texts, labels, ["sale", "rent"], "deal_type")

        # Deterministic training output
        self.assertEqual(m1, m2)

        # High confidence prediction for unseen text
        p = predict(m1, "ქირავდება ბინა მე-5 სართულზე")
        self.assertGreaterEqual(p["rent"], 0.9)

    def test_text_without_signal_is_not_confident(self):
        texts = []
        labels = []
        for i in range(40):
            texts.append(f"იყიდება {i + 1} ოთახიანი ბინა {50 + i} კვ.მ ფასი {80000 + i * 1000}$")
            labels.append("sale")
            texts.append(
                f"ქირავდება {i + 1} ოთახიანი ბინა {50 + i} კვ.მ ფასი {500 + i * 50}$ თვეში"
            )
            labels.append("rent")
        m = train(texts, labels, ["sale", "rent"], "deal_type")
        p = predict(m, "დარეკეთ ტელეფონის ნომერზე")
        self.assertGreater(p["rent"], 0.3)
        self.assertLess(p["rent"], 0.7)
        self.assertEqual(m["version"], 2)

    def test_pick_threshold(self):
        # Case 1: High accuracy starting at 0.50 with >= 50 samples
        probs = [0.96] * 60
        correct = [True] * 60
        t1 = pick_threshold(probs, correct, 0.95)
        self.assertEqual(t1, 0.50)

        # Case 2: Low accuracy at 0.50..0.85, but at 0.90 accuracy >= target with >= 50 samples
        probs2 = [0.85] * 40 + [0.92] * 60
        correct2 = [False] * 40 + [
            True
        ] * 60  # at <=0.85: 60/100 = 0.60; at 0.90: 60/60 = 1.0 >= 0.95
        t2 = pick_threshold(probs2, correct2, 0.95)
        self.assertEqual(t2, 0.90)

        # Case 3: Gate fails (all wrong, target never met)
        probs3 = [0.95] * 60
        correct3 = [False] * 60
        t3 = pick_threshold(probs3, correct3, 0.95)
        self.assertIsNone(t3)

    def test_test_split(self):
        self.assertIsInstance(test_split("12345"), bool)
        self.assertEqual(test_split("12345"), test_split("12345"))
        # Split roughly 20%
        splits = [test_split(str(i)) for i in range(1000)]
        ratio = sum(splits) / len(splits)
        self.assertAlmostEqual(ratio, 0.2, delta=0.05)


if __name__ == "__main__":
    unittest.main()
