import os
import sys
import argparse
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple, Optional

import numpy as np
import pandas as pd
import xgboost as xgb

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.domain.models import ClosedTrade
from app.ml_pipeline.bootstrap import (
    FEATURE_COLUMNS,
    HistoricalModelBootstrapService,
    SolanaRpcHistoricalTransactionSource,
    ReconstructedPosition
)
from app.ml_pipeline.training_utils import compute_class_sample_weights, stratified_train_test_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("walk_forward_eval")


def compute_multiclass_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, Dict[str, float]]:
    """Calculates per-class Precision, Recall, and F1 score for classes [0: HOLD, 1: BUY_BENAR, 2: SALAH]."""
    metrics = {}
    class_names = {0: "HOLD", 1: "BUY_BENAR", 2: "SALAH"}

    for c_idx, c_name in class_names.items():
        tp = int(np.sum((y_pred == c_idx) & (y_true == c_idx)))
        fp = int(np.sum((y_pred == c_idx) & (y_true != c_idx)))
        fn = int(np.sum((y_pred != c_idx) & (y_true == c_idx)))

        precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = float(2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        metrics[c_name] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn
        }
    return metrics


def run_walk_forward_eval(
    window_days: int = 14,
    step_days: int = 7,
    confidence_threshold: float = 0.75,
    max_sigs_override: Optional[int] = None,
    positions_override: Optional[List[ReconstructedPosition]] = None
) -> Tuple[Optional[pd.DataFrame], List[Dict[str, Any]]]:
    """
    Executes Out-of-Time Walk-Forward Evaluation operating DIRECTLY on ReconstructedPosition objects.
    Enforces anti-look-ahead bias on features and computes per-class Precision/Recall/F1 & threshold sensitivity.
    """
    logger.info(f"[GAP-7 WALK-FORWARD] Starting evaluation (Train Window: {window_days}d, Step: {step_days}d)")

    # 1. Acquire ReconstructedPosition dataset (Primary Data Source)
    svc = HistoricalModelBootstrapService(
        transaction_source=SolanaRpcHistoricalTransactionSource(
            max_signatures_per_wallet=max_sigs_override
        )
    )

    if positions_override is not None:
        positions = positions_override
        logger.info(f"[GAP-7 WALK-FORWARD] Using {len(positions)} injected ReconstructedPositions for evaluation.")
    else:
        logger.info("[GAP-7 WALK-FORWARD] Fetching historical on-chain events via Solana RPC...")
        try:
            import asyncio
            wallet_events = asyncio.run(svc._fetch_historical_events())
            positions = asyncio.run(svc._reconstruct_positions(wallet_events)) if wallet_events else []
        except Exception as exc:
            logger.error(f"[GAP-7 WALK-FORWARD] Failed to reconstruct positions from RPC: {exc}")
            positions = []

    if not positions:
        logger.warning("[GAP-7 WALK-FORWARD] [LIMITATION] No ReconstructedPositions available for walk-forward evaluation.")
        return None, []

    # Sort positions chronologically by entry_ts (Strict anti-look-ahead sorting)
    sorted_positions: List[ReconstructedPosition] = sorted(
        positions,
        key=lambda p: p.entry_ts.replace(tzinfo=timezone.utc) if p.entry_ts.tzinfo is None else p.entry_ts
    )

    min_time = sorted_positions[0].entry_ts.replace(tzinfo=timezone.utc) if sorted_positions[0].entry_ts.tzinfo is None else sorted_positions[0].entry_ts
    max_time = sorted_positions[-1].entry_ts.replace(tzinfo=timezone.utc) if sorted_positions[-1].entry_ts.tzinfo is None else sorted_positions[-1].entry_ts

    total_days = (max_time - min_time).total_seconds() / 86400.0
    logger.info(
        f"[GAP-7 WALK-FORWARD] Dataset contains {len(sorted_positions)} positions spanning "
        f"{min_time.strftime('%Y-%m-%d')} to {max_time.strftime('%Y-%m-%d')} ({total_days:.1f} days)."
    )

    # Calculate total mathematical windows possible
    possible_windows = int(max(0, (total_days - window_days) // step_days + 1))
    if possible_windows < 3:
        logger.warning(
            f"[GAP-7 WALK-FORWARD] [LIMITATION] Insufficient time span to form at least 3 walk-forward windows. "
            f"Span: {total_days:.1f}d, Required window: {window_days}d + {step_days}d step. "
            f"Possible windows: {possible_windows}. Reporting structural limitation."
        )

    # Pre-build feature vectors and labels using svc._feature_row() and svc._label_from_r_multiple()
    # maintaining chronological wallet stats and strict anti-look-ahead cluster window
    pos_data: List[Dict[str, Any]] = []
    wallet_stats: Dict[str, List[ClosedTrade]] = {}

    for idx, pos in enumerate(sorted_positions):
        entry_val = pos.amount_token * pos.entry_snapshot.price_usd
        exit_val = pos.amount_token * pos.exit_snapshot.price_usd
        if entry_val <= 0:
            continue

        pnl_pct = (exit_val - entry_val) / entry_val
        r_mult = pnl_pct / settings.RISK_PCT_PER_TRADE
        label_str, label_idx = svc._label_from_r_multiple(r_mult)
        holding_mins = max(1, int((pos.exit_ts - pos.entry_ts).total_seconds() / 60.0))

        prior_trades = wallet_stats.get(pos.wallet_address, [])
        
        # Anti-look-ahead: cluster_score events ONLY include positions with entry_ts <= target position entry_ts
        positions_up_to_now = [p for p in sorted_positions[:idx + 1]]

        feature_dict = svc._feature_row(
            position=pos,
            entry_value=entry_val,
            holding_minutes=holding_mins,
            prior_trades=prior_trades,
            all_positions=positions_up_to_now
        )

        closed_trade_repr = ClosedTrade(
            trade_id=f"wf_{pos.entry_signature[:8]}_{idx}",
            wallet_source=pos.wallet_address,
            token_address=pos.token_mint,
            token_symbol=pos.token_mint[:8],
            signal_ts=pos.entry_ts,
            entry_ts=pos.entry_ts,
            exit_ts=pos.exit_ts,
            direction="BUY",
            confidence_score=0.0,
            safety_check_passed=True,
            entry_price=pos.entry_snapshot.price_usd,
            exit_price=pos.exit_snapshot.price_usd,
            position_size_usd=float(entry_val),
            risk_pct=settings.RISK_PCT_PER_TRADE,
            pnl_pct_actual=float(pnl_pct),
            r_multiple=float(r_mult),
            label=label_str,
            holding_time_minutes=holding_mins,
            exit_reason="bootstrap_reconstructed",
            is_paper_trade=True,
            is_bootstrap=True,
            model_version="v0",
            slippage_actual=0.01
        )

        wallet_stats.setdefault(pos.wallet_address, []).append(closed_trade_repr)

        pos_data.append({
            "position": pos,
            "feature_dict": feature_dict,
            "label_str": label_str,
            "label_idx": label_idx,
            "r_multiple": r_mult,
            "entry_ts": pos.entry_ts.replace(tzinfo=timezone.utc) if pos.entry_ts.tzinfo is None else pos.entry_ts
        })

    fold_results: List[Dict[str, Any]] = []
    current_start = min_time

    fold_idx = 1
    while current_start + timedelta(days=window_days + step_days) <= max_time + timedelta(days=step_days):
        train_end = current_start + timedelta(days=window_days)
        test_end = train_end + timedelta(days=step_days)

        train_data = [pd_item for pd_item in pos_data if current_start <= pd_item["entry_ts"] < train_end]
        test_data = [pd_item for pd_item in pos_data if train_end <= pd_item["entry_ts"] < test_end]

        if len(train_data) < 5 or len(test_data) < 2:
            current_start += timedelta(days=step_days)
            continue

        train_X = pd.DataFrame([item["feature_dict"] for item in train_data], columns=FEATURE_COLUMNS)
        train_y = np.array([item["label_idx"] for item in train_data])

        test_X = pd.DataFrame([item["feature_dict"] for item in test_data], columns=FEATURE_COLUMNS)
        test_y = np.array([item["label_idx"] for item in test_data])

        # Train model with stratified train/val split
        X_tr, X_val, y_tr, y_val = stratified_train_test_split(train_X.to_numpy(), train_y, test_size=0.20, random_state=42)
        dtrain = xgb.DMatrix(X_tr, label=y_tr, weight=compute_class_sample_weights(y_tr, num_class=3))
        
        params = {
            "max_depth": 6,
            "learning_rate": 0.05,
            "objective": "multi:softprob",
            "num_class": 3,
            "seed": 42,
            "tree_method": "hist"
        }
        model = xgb.train(params, dtrain, num_boost_round=150)

        # Predict out-of-time test set
        dtest = xgb.DMatrix(test_X.to_numpy())
        test_probs = model.predict(dtest)
        preds = np.argmax(test_probs, axis=1)

        # 1. Overall Accuracy
        correct = np.sum(preds == test_y)
        acc = float(correct / len(test_y))

        # 2. Per-Class Precision, Recall, F1
        class_metrics = compute_multiclass_metrics(test_y, preds)

        # 3. Confidence Threshold Sensitivity Analysis (0.50, 0.60, 0.70)
        threshold_sensitivity = {}
        for th in [0.50, 0.60, 0.70]:
            pass_mask = (preds == 1) & (test_probs[:, 1] >= th)
            threshold_sensitivity[f"signals_th_{int(th*100)}"] = int(np.sum(pass_mask))

        # 4. Expectancy R for signals at requested confidence_threshold
        pass_mask_target = (preds == 1) & (test_probs[:, 1] >= confidence_threshold)
        triggered_r = [item["r_multiple"] for i, item in enumerate(test_data) if pass_mask_target[i]]
        expectancy_r = float(np.mean(triggered_r)) if triggered_r else 0.0

        fold_results.append({
            "fold": fold_idx,
            "train_period": f"{current_start.strftime('%m/%d')}-{train_end.strftime('%m/%d')}",
            "test_period": f"{train_end.strftime('%m/%d')}-{test_end.strftime('%m/%d')}",
            "train_n": len(train_data),
            "test_n": len(test_data),
            "accuracy": acc,
            "buy_benar_precision": class_metrics["BUY_BENAR"]["precision"],
            "buy_benar_recall": class_metrics["BUY_BENAR"]["recall"],
            "buy_benar_f1": class_metrics["BUY_BENAR"]["f1"],
            "salah_precision": class_metrics["SALAH"]["precision"],
            "salah_recall": class_metrics["SALAH"]["recall"],
            "salah_f1": class_metrics["SALAH"]["f1"],
            "expectancy_r": expectancy_r,
            "signals_target_th": len(triggered_r),
            "th_sensitivity": threshold_sensitivity
        })

        fold_idx += 1
        current_start += timedelta(days=step_days)

    if not fold_results:
        logger.warning("[GAP-7 WALK-FORWARD] No walk-forward folds had sufficient data for evaluation.")
        return None, []

    df_res = pd.DataFrame(fold_results)
    print("\n" + "=" * 95)
    print("WALK-FORWARD OUT-OF-TIME EVALUATION REPORT (GAP 7 — 100% REAL RECONSTRUCTED POSITIONS)")
    print("=" * 95)
    print(df_res[["fold", "train_period", "test_period", "train_n", "test_n", "accuracy",
                  "buy_benar_precision", "buy_benar_recall", "buy_benar_f1", "expectancy_r", "signals_target_th"]].to_string(index=False))
    print("-" * 95)
    print(f"Mean Accuracy            : {df_res['accuracy'].mean():.1%}")
    print(f"Mean BUY_BENAR Precision : {df_res['buy_benar_precision'].mean():.1%}")
    print(f"Mean BUY_BENAR Recall    : {df_res['buy_benar_recall'].mean():.1%}")
    print(f"Mean BUY_BENAR F1        : {df_res['buy_benar_f1'].mean():.1%}")
    print(f"Mean SALAH Precision     : {df_res['salah_precision'].mean():.1%}")
    print(f"Mean Expectancy R        : {df_res['expectancy_r'].mean():+.2f}R")
    print("=" * 95 + "\n")

    return df_res, fold_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-Forward Out-of-Time Evaluation Script (GAP 7)")
    parser.add_argument("--window_days", type=int, default=14, help="Days in rolling training window")
    parser.add_argument("--step_days", type=int, default=7, help="Days to step forward for test window")
    parser.add_argument("--confidence", type=float, default=0.75, help="Confidence threshold")
    parser.add_argument(
        "--max-sigs", type=int, default=None,
        dest="max_sigs",
        help="Max RPC signatures to fetch per wallet"
    )
    args = parser.parse_args()

    run_walk_forward_eval(
        window_days=args.window_days,
        step_days=args.step_days,
        confidence_threshold=args.confidence,
        max_sigs_override=args.max_sigs if args.max_sigs else settings.BOOTSTRAP_MAX_SIGNATURES_PER_WALLET,
    )
