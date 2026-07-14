"""Tiny dependency-free linear regression experiment for Demo Day."""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path


def make_dataset(seed: int, size: int) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    samples: list[tuple[float, float]] = []
    for index in range(size):
        x = -1.0 + 2.0 * index / max(size - 1, 1)
        noise = rng.uniform(-0.04, 0.04)
        y = 2.0 * x - 1.0 + noise
        samples.append((x, y))
    return samples


def evaluate(samples: list[tuple[float, float]], weight: float, bias: float) -> tuple[float, float]:
    squared_error = 0.0
    absolute_error = 0.0
    for x, y in samples:
        prediction = weight * x + bias
        error = prediction - y
        squared_error += error * error
        absolute_error += abs(error)
    count = len(samples)
    return squared_error / count, absolute_error / count


def train(config: dict[str, float | int], *, smoke: bool) -> tuple[float, float, float, float]:
    seed = int(config.get("seed", 42))
    epochs = 1 if smoke else int(config.get("epochs", 8))
    learning_rate = float(config.get("learning_rate", 0.1))
    sample_count = int(config.get("samples", 24))
    samples = make_dataset(seed, sample_count)

    weight = 0.0
    bias = 0.0
    print(f"seed={seed} learning_rate={learning_rate:g} samples={sample_count}", flush=True)
    for epoch in range(1, epochs + 1):
        grad_weight = 0.0
        grad_bias = 0.0
        for x, y in samples:
            error = weight * x + bias - y
            grad_weight += 2.0 * error * x / sample_count
            grad_bias += 2.0 * error / sample_count
        weight -= learning_rate * grad_weight
        bias -= learning_rate * grad_bias
        loss, mae = evaluate(samples, weight, bias)
        print(
            f"epoch={epoch} loss={loss:.6f} mae={mae:.6f} "
            f"weight={weight:.6f} bias={bias:.6f}",
            flush=True,
        )
        time.sleep(0.05)
    return weight, bias, *evaluate(samples, weight, bias)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="demo_project/config.json")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--lr", type=float, default=None, help="override learning_rate")
    parser.add_argument("--epochs", type=int, default=None, help="override epochs")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if args.lr is not None:
        config["learning_rate"] = args.lr
    if args.epochs is not None:
        config["epochs"] = args.epochs

    weight, bias, loss, mae = train(config, smoke=args.smoke)
    artifact = {
        "task": "linear_regression",
        "target": "y = 2*x - 1 + noise",
        "weight": round(weight, 6),
        "bias": round(bias, 6),
        "loss": round(loss, 6),
        "mae": round(mae, 6),
    }
    Path("demo_project/model.txt").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("status=completed", flush=True)


if __name__ == "__main__":
    main()
