"""A tiny one-dimensional linear regressor."""
from __future__ import annotations


class LinearRegressor:
    def __init__(self) -> None:
        self.slope = 0.0
        self.intercept = 0.0

    def fit(self, features: list[float], targets: list[float]) -> None:
        if len(features) != len(targets):
            raise ValueError("training features and targets must have equal length")
        if len(features) < 2:
            raise ValueError("at least two training examples are required")

        mean_x = sum(features) / len(features)
        mean_y = sum(targets) / len(targets)
        denominator = sum((value - mean_x) ** 2 for value in features)
        if denominator == 0:
            raise ValueError("training features must not all be identical")
        self.slope = sum(
            (value - mean_x) * (target - mean_y)
            for value, target in zip(features, targets)
        ) / denominator
        self.intercept = mean_y - self.slope * mean_x

    def predict(self, features: list[float]) -> list[float]:
        return [self.slope * value + self.intercept for value in features]
