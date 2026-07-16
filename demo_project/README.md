# Demo Project

This is a tiny dependency-free experiment for Demo Day. It trains a handwritten
linear regression model on deterministic synthetic data:

```text
y = 2*x - 1 + noise
```

The script prints per-epoch `loss`, `mae`, `weight`, and `bias`, then writes the
final model summary to `demo_project/model.txt`.

```powershell
python demo_project/train.py --smoke
python demo_project/train.py --config demo_project/config.json
python demo_project/train.py --config demo_project/config.json --lr 0.1 --epochs 3
```
