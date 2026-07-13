"""无需第三方依赖的短实验，用于 Demo Day 验证完整科研工作流。"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="demo_project/config.json")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    random.seed(config["seed"])
    epochs = 1 if args.smoke else int(config["epochs"])
    print(f"seed={config['seed']}", flush=True)
    for epoch in range(1, epochs + 1):
        loss = 1.0 / (epoch + 1)
        accuracy = 0.60 + 0.08 * epoch
        print(f"epoch={epoch} loss={loss:.4f} accuracy={accuracy:.4f}", flush=True)
        time.sleep(0.15)
    Path("demo_project/model.txt").write_text("demo model artifact\n", encoding="utf-8")
    print("status=completed", flush=True)


if __name__ == "__main__":
    main()