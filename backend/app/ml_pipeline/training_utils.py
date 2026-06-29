import numpy as np


def compute_class_sample_weights(labels: np.ndarray, num_class: int = 3) -> np.ndarray:
    """
    Computes per-row inverse-frequency sample weights for multi-class XGBoost.

    XGBoost's `scale_pos_weight` is binary-only; for the multi-class objective
    used here, class balancing must be applied through DMatrix sample weights.
    """
    labels = np.asarray(labels)
    n_samples = len(labels)
    weights = np.ones(n_samples, dtype=float)

    if n_samples == 0:
        return weights

    for class_idx in range(num_class):
        class_mask = labels == class_idx
        class_count = int(class_mask.sum())
        if class_count == 0:
            continue
        class_weight = n_samples / (num_class * class_count)
        weights[class_mask] = class_weight

    return weights
