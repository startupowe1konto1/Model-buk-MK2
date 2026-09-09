"""Wieloklasowa regresja logistyczna na czystym stdlib.

Pelny gradient (batch), bo 86 tysiecy wierszy i dwie cechy to dla Pythona
sekundy, a batch jest deterministyczny - zaden SGD, zadne tasowanie, zadnego
ziarna do pilnowania.

Model celowo ubogi: [1.0, elo_diff/400]. Dokladanie cech to osobny przyrost,
ktory [[12 - Ewaluacja i backtesting]] kaze prowadzic jako Model_base vs
Model_base+grupa z hipoteza postawiona z gory.
"""
import math
from typing import List, Sequence, Tuple


def softmax(scores: Sequence[float]) -> Tuple[float, ...]:
    top = max(scores)  # stabilnosc numeryczna
    exps = [math.exp(s - top) for s in scores]
    total = sum(exps)
    return tuple(e / total for e in exps)


def predict_proba(weights: Sequence[Sequence[float]], x: Sequence[float]) -> Tuple[float, ...]:
    return softmax([sum(w * v for w, v in zip(row, x)) for row in weights])


def fit(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    n_classes: int = 3,
    lr: float = 0.5,
    epochs: int = 300,
    l2: float = 1e-4,
) -> List[List[float]]:
    """Zwraca macierz wag n_classes x n_features."""
    n = len(X)
    if n == 0:
        raise ValueError("pusty zbior treningowy")
    n_features = len(X[0])
    weights = [[0.0] * n_features for _ in range(n_classes)]

    for _ in range(epochs):
        grads = [[0.0] * n_features for _ in range(n_classes)]
        for xi, yi in zip(X, y):
            probs = predict_proba(weights, xi)
            for c in range(n_classes):
                err = probs[c] - (1.0 if c == yi else 0.0)
                if err == 0.0:
                    continue
                grow = grads[c]
                for j, v in enumerate(xi):
                    grow[j] += err * v
        for c in range(n_classes):
            for j in range(n_features):
                g = grads[c][j] / n
                if j > 0:  # nie regularyzujemy wyrazu wolnego
                    g += l2 * weights[c][j]
                weights[c][j] -= lr * g
    return weights
