"""Evaluation metrics with defensive validation."""
from __future__ import annotations


def mean_absolute_error(predictions: list[float], targets: list[float]) -> float:
    if len(predictions) != len(targets):
        raise ValueError(
            "prediction/target count mismatch: "
            f"{len(predictions)} != {len(targets)}. "
            "Check that preprocessing filters features and labels together."
        )
    if not predictions:
        raise ValueError("cannot evaluate an empty validation set")
    return sum(abs(prediction - target) for prediction, target in zip(predictions, targets)) / len(predictions)
