"""Explicit unittest regression checks for the B4 demo.

The file is not collected by repository-wide pytest because its initial state
is deliberately failing; run it with the command in the demo README.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

__test__ = False
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_pipeline import _build_xy, load_examples, prepare_datasets
from metrics import mean_absolute_error


class DataPipelineTests(unittest.TestCase):
    def test_missing_target_row_is_dropped_as_one_example(self):
        features, targets = _build_xy(load_examples()[-2:])

        self.assertEqual(features, [6.0])
        self.assertEqual(targets, [12.0])

    def test_validation_features_and_targets_remain_aligned(self):
        _, _, validation_x, validation_y = prepare_datasets(holdout_size=2)

        self.assertEqual(len(validation_x), len(validation_y))
        self.assertEqual(mean_absolute_error([12.0], validation_y), 0.0)


if __name__ == "__main__":
    unittest.main()
