"""Training entry point for the intentionally broken B4 demonstration."""
from __future__ import annotations

import json
from pathlib import Path

from data_pipeline import prepare_datasets
from metrics import mean_absolute_error
from model import LinearRegressor


def main() -> None:
    config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
    train_x, train_y, validation_x, validation_y = prepare_datasets(config["holdout_size"])

    model = LinearRegressor()
    model.fit(train_x, train_y)
    score = mean_absolute_error(model.predict(validation_x), validation_y)

    print(f"训练样本: {len(train_x)}")
    print(f"验证样本: {len(validation_x)}")
    print(f"验证 MAE: {score:.3f}")


if __name__ == "__main__":
    main()
