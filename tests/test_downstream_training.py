from __future__ import annotations

import random
import unittest

import numpy as np
import torch

from src.downstream.train_ag_news import (
    NewsTransformer,
    capture_rng_state,
    restore_rng_state,
    set_determinism,
)


class DownstreamTrainingTest(unittest.TestCase):
    def test_rng_state_round_trip_is_exact(self) -> None:
        device = torch.device("cpu")
        set_determinism(20260726)
        state = capture_rng_state(device)
        expected = (random.random(), float(np.random.random()), torch.rand(4))
        random.random()
        np.random.random()
        torch.rand(4)
        restore_rng_state(state, device)
        actual = (random.random(), float(np.random.random()), torch.rand(4))
        self.assertEqual(expected[0], actual[0])
        self.assertEqual(expected[1], actual[1])
        self.assertTrue(torch.equal(expected[2], actual[2]))

    def test_news_transformer_forward_shape_and_finite_logits(self) -> None:
        config = {
            "embedding_dim": 16,
            "max_sequence_length": 8,
            "attention_heads": 4,
            "ffn_dim": 32,
            "dropout": 0.0,
            "transformer_layers": 2,
        }
        model = NewsTransformer(vocab_size=32, pad_id=0, config=config)
        token_ids = torch.tensor(
            [[1, 4, 5, 2, 0, 0, 0, 0], [1, 7, 8, 9, 10, 2, 0, 0]], dtype=torch.long
        )
        logits = model(token_ids)
        self.assertEqual(tuple(logits.shape), (2, 4))
        self.assertTrue(bool(torch.isfinite(logits).all()))


if __name__ == "__main__":
    unittest.main()
