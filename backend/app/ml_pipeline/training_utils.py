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


def stratified_train_test_split(
    X: np.ndarray,
    y: np.ndarray,
    test_size: float = 0.20,
    random_state: int = 42
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Performs a deterministic stratified train/test split on features X and targets y.
    Preserves exact class proportions between training and validation sets.
    """
    X = np.asarray(X)
    y = np.asarray(y)
    rng = np.random.RandomState(random_state)

    train_indices = []
    val_indices = []

    unique_classes = np.unique(y)
    for c in unique_classes:
        cls_indices = np.where(y == c)[0]
        rng.shuffle(cls_indices)
        
        n_val = int(round(len(cls_indices) * test_size))
        if n_val == 0 and len(cls_indices) > 1:
            n_val = 1
            
        val_indices.extend(cls_indices[:n_val])
        train_indices.extend(cls_indices[n_val:])

    train_indices = np.array(train_indices, dtype=int)
    val_indices = np.array(val_indices, dtype=int)

    if len(train_indices) > 0:
        rng.shuffle(train_indices)
    if len(val_indices) > 0:
        rng.shuffle(val_indices)

    return X[train_indices], X[val_indices], y[train_indices], y[val_indices]

