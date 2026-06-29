import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import xgboost as xgb

from app.core.config import settings
from app.domain.interfaces import IModelBootstrapService, IModelRegistryRepository, ITradeHistoryRepository
from app.domain.models import ClosedTrade, ModelRegistry
from app.ml_pipeline.training_utils import compute_class_sample_weights

logger = logging.getLogger(__name__)


FEATURE_COLUMNS = [
    "position_size_usd",
    "token_age_minutes",
    "liquidity_pool_depth",
    "slippage_actual",
    "cluster_score",
    "win_rate_30d",
    "avg_holding_time_minutes",
    "typical_trade_size_usd",
    "past_exit_pattern_score",
    "sol_usd_momentum",
    "token_volume_liquidity_ratio",
    "hour_of_day_utc",
]


class BootstrapDataUnavailable(RuntimeError):
    pass


@dataclass
class HistoricalSwapEvent:
    wallet_address: str
    signature: str
    token_mint: str
    direction: str  # BUY | SELL
    amount_token: float
    timestamp: datetime


@dataclass
class TokenMarketSnapshot:
    price_usd: float
    liquidity_usd: float
    volume_24h: float
    pair_created_at: Optional[datetime] = None


@dataclass
class ReconstructedPosition:
    wallet_address: str
    token_mint: str
    entry_signature: str
    exit_signature: str
    entry_ts: datetime
    exit_ts: datetime
    amount_token: float
    entry_snapshot: TokenMarketSnapshot
    exit_snapshot: TokenMarketSnapshot


class SolanaRpcHistoricalTransactionSource:
    """
    Public/free Solana RPC adapter used as the block-explorer data source.
    It fetches wallet signatures and full parsed transactions, then extracts
    token balance deltas for the monitored wallet.
    """
    WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_signatures_per_wallet: Optional[int] = None,
    ):
        self.rpc_url = rpc_url or settings.SOLANA_RPC_URL
        self.timeout_seconds = timeout_seconds or settings.BOOTSTRAP_API_TIMEOUT_SECONDS
        self.max_signatures_per_wallet = max_signatures_per_wallet or settings.BOOTSTRAP_MAX_SIGNATURES_PER_WALLET

    async def fetch_wallet_events(self, wallet_address: str, history_days: int) -> List[HistoricalSwapEvent]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
        signatures = await self._fetch_signatures(wallet_address, cutoff)
        events: List[HistoricalSwapEvent] = []

        for signature in signatures:
            try:
                tx = await self._fetch_transaction(signature)
                if not tx:
                    continue
                events.extend(self._parse_transaction(wallet_address, signature, tx))
            except Exception as exc:
                logger.warning(
                    "[MODEL BOOTSTRAP] Skipping inconsistent transaction %s for %s: %s",
                    signature,
                    wallet_address,
                    exc,
                )

        events.sort(key=lambda e: e.timestamp)
        return events

    async def _fetch_signatures(self, wallet_address: str, cutoff: datetime) -> List[str]:
        import asyncio

        def sync_fetch() -> List[str]:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    wallet_address,
                    {"limit": min(self.max_signatures_per_wallet, 1000)},
                ],
            }
            response = self._post_json(payload)
            result = response.get("result")
            if not isinstance(result, list):
                raise BootstrapDataUnavailable(f"invalid signature response for {wallet_address}")

            signatures: List[str] = []
            for row in result:
                block_time = row.get("blockTime")
                if block_time is not None:
                    ts = datetime.fromtimestamp(block_time, timezone.utc)
                    if ts < cutoff:
                        continue
                sig = row.get("signature")
                if sig:
                    signatures.append(sig)
            return signatures

        try:
            return await asyncio.to_thread(sync_fetch)
        except Exception as exc:
            raise BootstrapDataUnavailable(f"block explorer unavailable for {wallet_address}: {exc}") from exc

    async def _fetch_transaction(self, signature: str) -> Optional[dict]:
        import asyncio

        def sync_fetch() -> Optional[dict]:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            }
            response = self._post_json(payload)
            result = response.get("result")
            return result if isinstance(result, dict) else None

        try:
            return await asyncio.to_thread(sync_fetch)
        except Exception as exc:
            logger.warning("[MODEL BOOTSTRAP] Failed fetching transaction %s: %s", signature, exc)
            return None

    def _post_json(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.rpc_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _parse_transaction(self, wallet_address: str, signature: str, tx: dict) -> List[HistoricalSwapEvent]:
        block_time = tx.get("blockTime")
        timestamp = (
            datetime.fromtimestamp(block_time, timezone.utc)
            if block_time
            else datetime.now(timezone.utc)
        )
        meta = tx.get("meta") or {}
        pre_balances = meta.get("preTokenBalances") or []
        post_balances = meta.get("postTokenBalances") or []

        before = self._balances_by_mint(pre_balances, wallet_address)
        after = self._balances_by_mint(post_balances, wallet_address)
        mints = set(before) | set(after)
        events: List[HistoricalSwapEvent] = []

        for mint in mints:
            if mint == self.WRAPPED_SOL_MINT:
                continue
            delta = after.get(mint, 0.0) - before.get(mint, 0.0)
            if abs(delta) <= 1e-12:
                continue
            events.append(
                HistoricalSwapEvent(
                    wallet_address=wallet_address,
                    signature=signature,
                    token_mint=mint,
                    direction="BUY" if delta > 0 else "SELL",
                    amount_token=abs(delta),
                    timestamp=timestamp,
                )
            )

        return events

    def _balances_by_mint(self, balances: list, wallet_address: str) -> Dict[str, float]:
        by_mint: Dict[str, float] = {}
        for row in balances:
            owner = row.get("owner")
            if owner and owner != wallet_address:
                continue
            mint = row.get("mint")
            if not mint:
                continue
            amount_info = row.get("uiTokenAmount") or {}
            ui_amount = amount_info.get("uiAmount")
            if ui_amount is None:
                amount_raw = amount_info.get("amount")
                decimals = int(amount_info.get("decimals") or 0)
                ui_amount = float(amount_raw or 0.0) / (10 ** decimals)
            by_mint[mint] = by_mint.get(mint, 0.0) + float(ui_amount or 0.0)
        return by_mint


class DexScreenerHistoricalPriceProvider:
    """
    DexScreener adapter for token market snapshots.

    DexScreener's public API is free and suitable for bootstrap enrichment.
    If a historical timestamp is not available from the public payload, the
    provider returns the best available pair snapshot and logs that limitation.
    """
    def __init__(self, timeout_seconds: Optional[float] = None):
        self.timeout_seconds = timeout_seconds or settings.BOOTSTRAP_API_TIMEOUT_SECONDS
        self.api_url = "https://api.dexscreener.com/latest/dex/tokens/"
        self.cache: Dict[str, Optional[TokenMarketSnapshot]] = {}

    async def get_snapshot(self, token_mint: str, timestamp: datetime) -> Optional[TokenMarketSnapshot]:
        import asyncio

        if token_mint in self.cache:
            return self.cache[token_mint]

        def sync_fetch() -> Optional[TokenMarketSnapshot]:
            req = urllib.request.Request(
                f"{self.api_url}{token_mint}",
                headers={"User-Agent": "Mozilla/5.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            pairs = payload.get("pairs") or []
            if not pairs:
                return None
            pair = sorted(
                pairs,
                key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0),
                reverse=True,
            )[0]
            created_at = pair.get("pairCreatedAt")
            pair_created_at = (
                datetime.fromtimestamp(created_at / 1000.0, timezone.utc)
                if created_at
                else None
            )
            price_usd = float(pair.get("priceUsd") or 0.0)
            if price_usd <= 0:
                return None
            return TokenMarketSnapshot(
                price_usd=price_usd,
                liquidity_usd=float((pair.get("liquidity") or {}).get("usd") or 0.0),
                volume_24h=float((pair.get("volume") or {}).get("h24") or 0.0),
                pair_created_at=pair_created_at,
            )

        try:
            snapshot = await asyncio.to_thread(sync_fetch)
            if snapshot:
                logger.warning(
                    "[MODEL BOOTSTRAP] DexScreener public payload used as best-effort snapshot "
                    "for %s at %s; exact historical OHLC is unavailable from this free endpoint.",
                    token_mint,
                    timestamp.isoformat(),
                )
            self.cache[token_mint] = snapshot
            return snapshot
        except Exception as exc:
            logger.warning("[MODEL BOOTSTRAP] DexScreener snapshot unavailable for %s: %s", token_mint, exc)
            self.cache[token_mint] = None
            return None


class HistoricalModelBootstrapService(IModelBootstrapService):
    """
    Builds Model v0 from historical on-chain wallet transactions.
    """
    def __init__(
        self,
        transaction_source: Optional[SolanaRpcHistoricalTransactionSource] = None,
        price_provider: Optional[DexScreenerHistoricalPriceProvider] = None,
        history_days: Optional[int] = None,
        min_trades_warning: Optional[int] = None,
    ):
        self.transaction_source = transaction_source or SolanaRpcHistoricalTransactionSource()
        self.price_provider = price_provider or DexScreenerHistoricalPriceProvider()
        self.history_days = history_days or settings.BOOTSTRAP_HISTORY_DAYS
        self.min_trades_warning = min_trades_warning or settings.BOOTSTRAP_MIN_TRADES_WARNING

    async def bootstrap_model_v0(
        self,
        models_dir: str,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo: Optional[ITradeHistoryRepository] = None,
    ) -> bool:
        logger.info("[MODEL BOOTSTRAP] Starting Model v0 historical bootstrap...")

        try:
            wallet_events = await self._fetch_historical_events()
        except BootstrapDataUnavailable as exc:
            logger.error("[MODEL BOOTSTRAP] Block explorer API unavailable: %s", exc)
            return False

        if not wallet_events:
            logger.error("[MODEL BOOTSTRAP] No historical wallet events found. Model v0 will not be trained.")
            return False

        positions = await self._reconstruct_positions(wallet_events)
        if not positions:
            logger.error("[MODEL BOOTSTRAP] No reconstructable positions found. Model v0 will not be trained.")
            return False

        if len(positions) < self.min_trades_warning:
            logger.warning(
                "[MODEL BOOTSTRAP] Only %s reconstructed trades available (< %s). "
                "Training v0 with limited historical coverage.",
                len(positions),
                self.min_trades_warning,
            )

        try:
            features, labels, closed_trades = self._build_training_dataset(positions)
            self._train_and_save_model(features, labels, models_dir)
            await self._persist_bootstrap_outputs(
                model_registry_repo,
                trade_history_repo,
                closed_trades,
                validation_accuracy=self._training_accuracy(features, labels, models_dir),
            )
        except Exception as exc:
            logger.error("[MODEL BOOTSTRAP] Training failed; system will continue without Model v0: %s", exc, exc_info=True)
            return False

        logger.info(
            "[MODEL BOOTSTRAP] Model v0 historical bootstrap complete with %s trades.",
            len(closed_trades),
        )
        return True

    async def _fetch_historical_events(self) -> List[HistoricalSwapEvent]:
        events: List[HistoricalSwapEvent] = []
        failures: List[str] = []

        for wallet in settings.TARGET_WALLETS:
            try:
                wallet_events = await self.transaction_source.fetch_wallet_events(wallet, self.history_days)
                events.extend(wallet_events)
            except BootstrapDataUnavailable as exc:
                failures.append(str(exc))

        if failures and not events:
            raise BootstrapDataUnavailable("; ".join(failures))

        if failures:
            logger.warning("[MODEL BOOTSTRAP] Some wallets could not be fetched: %s", "; ".join(failures))

        events.sort(key=lambda e: e.timestamp)
        return events

    async def _reconstruct_positions(self, events: List[HistoricalSwapEvent]) -> List[ReconstructedPosition]:
        open_lots: Dict[Tuple[str, str], List[HistoricalSwapEvent]] = {}
        positions: List[ReconstructedPosition] = []

        for event in events:
            key = (event.wallet_address, event.token_mint)
            if event.direction == "BUY":
                open_lots.setdefault(key, []).append(event)
                continue

            lots = open_lots.get(key, [])
            if not lots:
                logger.info(
                    "[MODEL BOOTSTRAP] Skipping unmatched historical sell %s for %s/%s",
                    event.signature,
                    event.wallet_address,
                    event.token_mint,
                )
                continue

            remaining_sell_amount = event.amount_token
            while lots and remaining_sell_amount > 1e-12:
                entry = lots[0]
                matched_amount = min(entry.amount_token, remaining_sell_amount)
                position = await self._build_position(entry, event, matched_amount)
                if position:
                    positions.append(position)

                entry.amount_token -= matched_amount
                remaining_sell_amount -= matched_amount
                if entry.amount_token <= 1e-12:
                    lots.pop(0)

        return positions

    async def _build_position(
        self,
        entry: HistoricalSwapEvent,
        exit_event: HistoricalSwapEvent,
        amount_token: float,
    ) -> Optional[ReconstructedPosition]:
        entry_snapshot = await self.price_provider.get_snapshot(entry.token_mint, entry.timestamp)
        exit_snapshot = await self.price_provider.get_snapshot(exit_event.token_mint, exit_event.timestamp)
        if not entry_snapshot or not exit_snapshot:
            logger.warning(
                "[MODEL BOOTSTRAP] Skipping %s/%s because price reconstruction is unavailable.",
                entry.wallet_address,
                entry.token_mint,
            )
            return None

        if exit_event.timestamp <= entry.timestamp:
            logger.warning(
                "[MODEL BOOTSTRAP] Skipping inconsistent position %s -> %s: exit before entry.",
                entry.signature,
                exit_event.signature,
            )
            return None

        return ReconstructedPosition(
            wallet_address=entry.wallet_address,
            token_mint=entry.token_mint,
            entry_signature=entry.signature,
            exit_signature=exit_event.signature,
            entry_ts=entry.timestamp,
            exit_ts=exit_event.timestamp,
            amount_token=amount_token,
            entry_snapshot=entry_snapshot,
            exit_snapshot=exit_snapshot,
        )

    def _build_training_dataset(
        self,
        positions: List[ReconstructedPosition],
    ) -> Tuple[pd.DataFrame, np.ndarray, List[ClosedTrade]]:
        feature_rows = []
        labels = []
        closed_trades: List[ClosedTrade] = []
        wallet_stats: Dict[str, List[ClosedTrade]] = {}

        for idx, position in enumerate(positions):
            entry_value = position.amount_token * position.entry_snapshot.price_usd
            exit_value = position.amount_token * position.exit_snapshot.price_usd
            if entry_value <= 0:
                logger.warning("[MODEL BOOTSTRAP] Skipping zero-value historical position %s", position.entry_signature)
                continue

            pnl_pct_actual = (exit_value - entry_value) / entry_value
            r_multiple = pnl_pct_actual / settings.RISK_PCT_PER_TRADE
            label, label_idx = self._label_from_r_multiple(r_multiple)
            holding_minutes = max(1, int((position.exit_ts - position.entry_ts).total_seconds() / 60.0))
            prior_trades = wallet_stats.get(position.wallet_address, [])
            feature_rows.append(self._feature_row(position, entry_value, holding_minutes, prior_trades))
            labels.append(label_idx)

            trade = ClosedTrade(
                trade_id=f"bt_{position.entry_signature[:8]}_{idx}",
                wallet_source=position.wallet_address,
                token_address=position.token_mint,
                token_symbol=position.token_mint[:8],
                signal_ts=position.entry_ts,
                entry_ts=position.entry_ts,
                exit_ts=position.exit_ts,
                direction="BUY",
                confidence_score=0.0,
                safety_check_passed=True,
                entry_price=position.entry_snapshot.price_usd,
                exit_price=position.exit_snapshot.price_usd,
                position_size_usd=float(entry_value),
                risk_pct=settings.RISK_PCT_PER_TRADE,
                pnl_pct_actual=float(pnl_pct_actual),
                r_multiple=float(r_multiple),
                label=label,
                holding_time_minutes=holding_minutes,
                exit_reason="bootstrap_reconstructed",
                is_paper_trade=True,
                is_bootstrap=True,
                model_version="v0",
            )
            closed_trades.append(trade)
            wallet_stats.setdefault(position.wallet_address, []).append(trade)

        if not feature_rows:
            raise ValueError("no feature rows created from reconstructed positions")

        return pd.DataFrame(feature_rows, columns=FEATURE_COLUMNS), np.asarray(labels, dtype=int), closed_trades

    def _feature_row(
        self,
        position: ReconstructedPosition,
        entry_value: float,
        holding_minutes: int,
        prior_trades: List[ClosedTrade],
    ) -> Dict[str, float]:
        if prior_trades:
            win_rate = sum(1 for t in prior_trades if t.label == "BUY_BENAR") / len(prior_trades)
            avg_hold = sum(t.holding_time_minutes for t in prior_trades) / len(prior_trades)
            typical_size = sum(t.position_size_usd for t in prior_trades) / len(prior_trades)
            exit_pattern = sum(1 for t in prior_trades if t.exit_reason.startswith("kill_switch")) / len(prior_trades)
        else:
            win_rate = 0.45
            avg_hold = 20.0
            typical_size = 500.0
            exit_pattern = 0.0

        pair_created_at = position.entry_snapshot.pair_created_at
        token_age_minutes = 60.0
        if pair_created_at:
            token_age_minutes = max(0.0, (position.entry_ts - pair_created_at).total_seconds() / 60.0)

        liquidity = max(0.0, position.entry_snapshot.liquidity_usd)
        volume_ratio = position.entry_snapshot.volume_24h / liquidity if liquidity > 0 else 0.0

        return {
            "position_size_usd": float(entry_value),
            "token_age_minutes": float(token_age_minutes),
            "liquidity_pool_depth": float(liquidity),
            "slippage_actual": 0.01,
            "cluster_score": 1.0,
            "win_rate_30d": float(max(0.0, min(win_rate, 1.0))),
            "avg_holding_time_minutes": float(avg_hold),
            "typical_trade_size_usd": float(typical_size),
            "past_exit_pattern_score": float(exit_pattern),
            "sol_usd_momentum": 0.0,
            "token_volume_liquidity_ratio": float(volume_ratio),
            "hour_of_day_utc": float(position.entry_ts.hour),
        }

    def _label_from_r_multiple(self, r_multiple: float) -> Tuple[str, int]:
        if r_multiple >= settings.LABELING_BUY_BENAR_THRESHOLD_R:
            return "BUY_BENAR", 1
        if r_multiple <= settings.LABELING_SALAH_THRESHOLD_R:
            return "SALAH", 2
        return "HOLD", 0

    def _train_and_save_model(self, features: pd.DataFrame, labels: np.ndarray, models_dir: str) -> None:
        os.makedirs(models_dir, exist_ok=True)
        dtrain = xgb.DMatrix(features, label=labels, weight=compute_class_sample_weights(labels, num_class=3))
        params = {
            "max_depth": 6,
            "learning_rate": 0.05,
            "objective": "multi:softprob",
            "num_class": 3,
            "seed": 42,
            "tree_method": "hist",
        }
        model = xgb.train(params, dtrain, num_boost_round=300)
        model.save_model(os.path.join(models_dir, "v0.json"))

    def _training_accuracy(self, features: pd.DataFrame, labels: np.ndarray, models_dir: str) -> float:
        model = xgb.Booster()
        model.load_model(os.path.join(models_dir, "v0.json"))
        preds = np.argmax(model.predict(xgb.DMatrix(features)), axis=1)
        return float(np.sum(preds == labels) / len(labels))

    async def _persist_bootstrap_outputs(
        self,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo: Optional[ITradeHistoryRepository],
        closed_trades: List[ClosedTrade],
        validation_accuracy: float,
    ) -> None:
        existing_v0 = await model_registry_repo.get_model_version("v0")
        registry_entry = ModelRegistry(
            model_version="v0",
            trained_at=datetime.now(timezone.utc),
            training_sample_count=len(closed_trades),
            validation_accuracy=validation_accuracy,
            expectancy_r=self._expectancy_r(closed_trades),
            is_active=True,
            rolled_back=False,
        )

        if existing_v0:
            existing_v0.trained_at = registry_entry.trained_at
            existing_v0.training_sample_count = registry_entry.training_sample_count
            existing_v0.validation_accuracy = registry_entry.validation_accuracy
            existing_v0.expectancy_r = registry_entry.expectancy_r
            existing_v0.is_active = True
            existing_v0.rolled_back = False
            await model_registry_repo.update_model_version(existing_v0)
        else:
            await model_registry_repo.add_model_version(registry_entry)

        if trade_history_repo:
            for trade in closed_trades:
                await trade_history_repo.add_closed_trade(trade)

    def _expectancy_r(self, trades: List[ClosedTrade]) -> float:
        if not trades:
            return 0.0
        winners = [t.r_multiple for t in trades if t.r_multiple > 0]
        losses = [abs(t.r_multiple) for t in trades if t.r_multiple <= 0]
        win_rate = len(winners) / len(trades)
        avg_win = sum(winners) / len(winners) if winners else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 1.0
        return float((win_rate * avg_win) - ((1.0 - win_rate) * avg_loss))
