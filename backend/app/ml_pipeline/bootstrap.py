import asyncio
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
    amount_usd: float = 0.0


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
        self.sol_price_history = []
        self._fetch_sol_price_history()

    def _fetch_sol_price_history(self):
        import urllib.request
        import json
        try:
            url = "https://api.coingecko.com/api/v3/coins/solana/market_chart?vs_currency=usd&days=30"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
                self.sol_price_history = data.get("prices", [])
                logger.info(f"[MODEL BOOTSTRAP] Loaded {len(self.sol_price_history)} historical SOL price points from CoinGecko")
        except Exception as e:
            logger.warning(f"[MODEL BOOTSTRAP] Could not fetch SOL price history from CoinGecko: {e}. Using fallback $145.0")

    def get_historical_sol_price(self, dt: datetime) -> float:
        if not self.sol_price_history:
            return 145.0
        ts_ms = int(dt.timestamp() * 1000)
        try:
            closest = min(self.sol_price_history, key=lambda x: abs(x[0] - ts_ms))
            return float(closest[1])
        except Exception:
            return 145.0

    async def fetch_wallet_events(self, wallet_address: str, history_days: int) -> List[HistoricalSwapEvent]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
        signatures = await self._fetch_signatures(wallet_address, cutoff)
        
        # Batch fetch transactions
        txs = await self._fetch_transactions_batch(signatures)
        
        events: List[HistoricalSwapEvent] = []
        for signature, tx in zip(signatures, txs):
            if not tx:
                continue
            try:
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

    async def _fetch_transactions_batch(self, signatures: List[str]) -> List[Optional[dict]]:
        import asyncio
        if not signatures:
            return []

        def sync_fetch_batch(batch_sigs: List[str]) -> List[Optional[dict]]:
            payload = [
                {
                    "jsonrpc": "2.0",
                    "id": idx,
                    "method": "getTransaction",
                    "params": [
                        sig,
                        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                    ],
                }
                for idx, sig in enumerate(batch_sigs)
            ]
            response = self._post_json(payload)
            results = [None] * len(batch_sigs)
            if isinstance(response, list):
                for item in response:
                    idx = item.get("id")
                    res = item.get("result")
                    if isinstance(idx, int) and 0 <= idx < len(batch_sigs):
                        results[idx] = res if isinstance(res, dict) else None
            elif isinstance(response, dict) and "result" in response:
                results[0] = response["result"]
            return results

        # Split signatures into batches of 5 (much safer than 20)
        batch_size = 5
        batches = [signatures[i:i + batch_size] for i in range(0, len(signatures), batch_size)]
        all_results = []
        
        for batch in batches:
            try:
                # Helius free/dev tier explicitly forbids JSON-RPC batching (returns 403 Forbidden).
                # We skip batching and raise an exception to fall back to individual fetches immediately.
                if "helius" in self.rpc_url.lower():
                    raise ValueError("Helius free tier does not support JSON-RPC batching.")
                res = await asyncio.to_thread(sync_fetch_batch, batch)
                all_results.extend(res)
                await asyncio.sleep(0.15)
            except Exception as e:
                logger.debug(f"[MODEL BOOTSTRAP] Batch of {len(batch)} failed: {e}. Falling back to individual fetches...")
                # Fallback: fetch one-by-one for this failed batch
                is_helius = "helius" in self.rpc_url.lower()
                for sig in batch:
                    try:
                        tx = await self._fetch_transaction(sig)
                        all_results.append(tx)
                        # Sleep longer for Helius to respect the 10 reqs/sec free limit and avoid 429s
                        await asyncio.sleep(0.35 if is_helius else 0.1)
                    except Exception as fallback_err:
                        logger.debug(f"[MODEL BOOTSTRAP] Individual fallback fetch failed for {sig[:12]}...: {fallback_err}")
                        all_results.append(None)
                
        return all_results



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

    def _post_json(self, payload: any) -> any:
        import socket
        import time
        import urllib.error

        retries = 5
        backoff = 2.0
        # Retryable error codes: 408=timeout, 429=rate limit, 502/503/504=server errors
        RETRYABLE_CODES = {408, 429, 502, 503, 504}
        # RPC fallback chain: primary -> secondary -> public fallback
        rpc_fallback_chain = [
            settings.RPC_PRIMARY_URL,
            settings.RPC_SECONDARY_URL,
            "https://api.mainnet-beta.solana.com",
        ]
        # Start from current configured URL
        current_rpc_idx = 0
        if self.rpc_url in rpc_fallback_chain:
            current_rpc_idx = rpc_fallback_chain.index(self.rpc_url)
        active_url = self.rpc_url

        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    active_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body)
            except (urllib.error.HTTPError, json.JSONDecodeError, ValueError) as err:
                # Catch JSONDecodeError (ValueError) to handle truncated payloads as retryable
                is_http_err = isinstance(err, urllib.error.HTTPError)
                if (not is_http_err or err.code in RETRYABLE_CODES) and attempt < retries - 1:
                    error_label = f"HTTP {err.code}" if is_http_err else "JSONDecodeError (truncated payload)"
                    logger.warning(
                        "[MODEL BOOTSTRAP] RPC %s on %s. Retrying in %.1fs (attempt %d/%d)...",
                        error_label,
                        active_url,
                        backoff,
                        attempt + 1,
                        retries,
                    )
                    time.sleep(backoff)
                    backoff *= 2.0
                    if attempt >= 1 and current_rpc_idx + 1 < len(rpc_fallback_chain):
                        current_rpc_idx += 1
                        active_url = rpc_fallback_chain[current_rpc_idx]
                        logger.warning(
                            "[MODEL BOOTSTRAP] Switching bootstrap RPC to fallback: %s",
                            active_url,
                        )
                else:
                    raise
            except (socket.timeout, TimeoutError) as err:
                if attempt < retries - 1:
                    logger.warning(
                        "[MODEL BOOTSTRAP] Socket timeout on %s. Retrying in %.1fs (attempt %d/%d)...",
                        active_url,
                        backoff,
                        attempt + 1,
                        retries,
                    )
                    time.sleep(backoff)
                    backoff *= 2.0
                    if attempt >= 1 and current_rpc_idx + 1 < len(rpc_fallback_chain):
                        current_rpc_idx += 1
                        active_url = rpc_fallback_chain[current_rpc_idx]
                        logger.warning(
                            "[MODEL BOOTSTRAP] Switching bootstrap RPC to fallback: %s",
                            active_url,
                        )

                else:
                    raise
            except Exception:
                raise


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

        before = self._balances_by_mint(pre_balances, wallet_address, tx)
        after = self._balances_by_mint(post_balances, wallet_address, tx)
        
        # Calculate SOL native changes
        transaction = tx.get("transaction") or {}
        message = transaction.get("message") or {}
        account_keys_raw = message.get("accountKeys") or []
        account_keys = []
        for k in account_keys_raw:
            if isinstance(k, dict):
                pubkey = k.get("pubkey")
                if pubkey:
                    account_keys.append(pubkey)
            elif isinstance(k, str):
                account_keys.append(k)

        pre_bals = meta.get("preBalances") or []
        post_bals = meta.get("postBalances") or []
        sol_change = 0.0
        try:
            wallet_idx = account_keys.index(wallet_address)
            if wallet_idx < len(pre_bals) and wallet_idx < len(post_bals):
                sol_change = (post_bals[wallet_idx] - pre_bals[wallet_idx]) / 1e9
        except ValueError:
            pass

        # WSOL change
        wsol_before = before.get(self.WRAPPED_SOL_MINT, 0.0)
        wsol_after = after.get(self.WRAPPED_SOL_MINT, 0.0)
        wsol_change = wsol_after - wsol_before

        # USDC change
        usdc_before = before.get("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 0.0)
        usdc_after = after.get("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 0.0)
        usdc_change = usdc_after - usdc_before

        # USDT change
        usdt_before = before.get("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 0.0)
        usdt_after = after.get("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 0.0)
        usdt_change = usdt_after - usdt_before

        # Total SOL change (native + wrapped)
        net_sol_change = sol_change + wsol_change
        
        # Estimate transaction USD value
        sol_price = self.get_historical_sol_price(timestamp)
        amount_usd = 0.0
        
        if abs(usdc_change) > 0.01 or abs(usdt_change) > 0.01:
            amount_usd = abs(usdc_change) + abs(usdt_change)
        elif abs(net_sol_change) > 0.001:
            amount_usd = abs(net_sol_change) * sol_price
            
        mints = set(before) | set(after)
        events: List[HistoricalSwapEvent] = []

        for mint in mints:
            if mint in [self.WRAPPED_SOL_MINT, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]:
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
                    amount_usd=amount_usd,
                )
            )

        return events

    def _balances_by_mint(self, balances: list, wallet_address: str, tx: Optional[dict] = None) -> Dict[str, float]:
        by_mint: Dict[str, float] = {}
        
        # Extract accountKeys as list of strings if tx is provided
        account_keys = []
        if tx:
            transaction = tx.get("transaction") or {}
            message = transaction.get("message") or {}
            account_keys_raw = message.get("accountKeys") or []
            for k in account_keys_raw:
                if isinstance(k, dict):
                    pubkey = k.get("pubkey")
                    if pubkey:
                        account_keys.append(pubkey)
                elif isinstance(k, str):
                    account_keys.append(k)

        for row in balances:
            owner = row.get("owner")
            if not owner and tx:
                acc_idx = row.get("accountIndex")
                if acc_idx is not None:
                    try:
                        idx = int(acc_idx)
                        if 0 <= idx < len(account_keys):
                            owner = account_keys[idx]
                    except (ValueError, IndexError):
                        pass

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
            if snapshot is None:
                raise ValueError("DexScreener returned empty pairs")
            logger.warning(
                "[MODEL BOOTSTRAP] DexScreener public payload used as best-effort snapshot "
                "for %s at %s; exact historical OHLC is unavailable from this free endpoint.",
                token_mint,
                timestamp.isoformat(),
            )
            self.cache[token_mint] = snapshot
            return snapshot
        except Exception as exc:
            if os.getenv("SIMULATION_MODE") == "True":
                logger.warning(
                    "[MODEL BOOTSTRAP] DexScreener snapshot unavailable for %s: %s. Using deterministic fallback (SIMULATION_MODE).",
                    token_mint,
                    exc,
                )
                import hashlib
                h_input = f"{token_mint}_{timestamp.isoformat()}"
                h_val = int(hashlib.md5(h_input.encode("utf-8")).hexdigest(), 16)
                price_usd = 0.90 + (h_val % 26) * 0.01
                
                snapshot = TokenMarketSnapshot(
                    price_usd=price_usd,
                    liquidity_usd=15000.0,
                    volume_24h=3000.0,
                    pair_created_at=datetime.now(timezone.utc) - timedelta(days=2),
                )
                self.cache[token_mint] = snapshot
                return snapshot
            else:
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
            except Exception as exc:
                failures.append(str(exc))

        if (failures and not events) or not events:
            if os.getenv("SIMULATION_MODE") == "True":
                logger.warning("[MODEL BOOTSTRAP] No historical events found from RPC. Generating synthetic swap dataset to bootstrap Model v0 (SIMULATION_MODE).")
                import random
                from datetime import timedelta
                
                mock_tokens = [
                    "DezXAZ8z7PnrnRJjz3wXBoRgixrfNg7yFLBnRx4S75Jb", # BONK
                    "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",  # WIF
                    "CzLSujW7ZJuY7oL4b5C32hiyUeZSt84b5F08Suj752b",
                    "9xQ1UvX4K9W1f3VjK3pQGWjhoxjq2WqU1AF9Z23J1x584",
                    "FX9mK3W1f3VjK3pQGWjhoxjq2WqU1AF9Z23J1x584hK"
                ]
                now = datetime.now(timezone.utc)
                for wallet in settings.TARGET_WALLETS:
                    for idx in range(30): # 30 round-trip trades = 60 events
                        token = random.choice(mock_tokens)
                        trade_time = now - timedelta(days=random.randint(1, 29))
                        exit_time = trade_time + timedelta(minutes=random.randint(5, 60))
                        amount = float(random.randint(100, 2000))
                        
                        # Buy event
                        events.append(HistoricalSwapEvent(
                            wallet_address=wallet,
                            signature=f"mock_sig_buy_{wallet[:4]}_{idx}_{int(trade_time.timestamp())}",
                            token_mint=token,
                            direction="BUY",
                            amount_token=amount,
                            timestamp=trade_time
                        ))
                        # Sell event
                        events.append(HistoricalSwapEvent(
                            wallet_address=wallet,
                            signature=f"mock_sig_sell_{wallet[:4]}_{idx}_{int(exit_time.timestamp())}",
                            token_mint=token,
                            direction="SELL",
                            amount_token=amount,
                            timestamp=exit_time
                        ))
            else:
                raise BootstrapDataUnavailable("; ".join(failures) if failures else "No historical events found from RPC.")

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
        entry_price = 0.0
        exit_price = 0.0
        
        if getattr(entry, "amount_usd", 0.0) > 0.0:
            entry_price = entry.amount_usd / entry.amount_token if entry.amount_token > 0 else 0.0
        else:
            entry_snap = await self.price_provider.get_snapshot(entry.token_mint, entry.timestamp)
            if entry_snap:
                entry_price = entry_snap.price_usd
                
        if getattr(exit_event, "amount_usd", 0.0) > 0.0:
            exit_price = exit_event.amount_usd / exit_event.amount_token if exit_event.amount_token > 0 else 0.0
        else:
            exit_snap = await self.price_provider.get_snapshot(exit_event.token_mint, exit_event.timestamp)
            if exit_snap:
                exit_price = exit_snap.price_usd

        if entry_price <= 0.0 or exit_price <= 0.0:
            logger.warning(
                "[MODEL BOOTSTRAP] Skipping %s/%s because price reconstruction is unavailable.",
                entry.wallet_address,
                entry.token_mint,
            )
            return None

        entry_snapshot = TokenMarketSnapshot(
            price_usd=entry_price,
            liquidity_usd=15000.0,
            volume_24h=3000.0,
            pair_created_at=entry.timestamp - timedelta(days=2),
        )
        exit_snapshot = TokenMarketSnapshot(
            price_usd=exit_price,
            liquidity_usd=15000.0,
            volume_24h=3000.0,
            pair_created_at=entry.timestamp - timedelta(days=2),
        )

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
            feature_rows.append(self._feature_row(position, entry_value, holding_minutes, prior_trades, all_positions=positions))
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
        all_positions: Optional[List[ReconstructedPosition]] = None,
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

        # Cluster score: compute using shared domain pure function
        from app.domain.cluster_logic import compute_cluster_score
        cluster_score = compute_cluster_score(
            target_wallet=position.wallet_address,
            target_token=position.token_mint,
            target_timestamp=position.entry_ts,
            events=all_positions or [],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        # STRUCTURAL LIMITATION — PERMANENT (not a conditional fallback):
        # Solana RPC historical data only contains post-execution results:
        #   amount_token  = actual tokens received (balance delta)
        #   amount_usd    = USD value of the swap (reconstructed from price snapshot)
        # The pre-swap quoted price (what was promised before execution) is NOT stored
        # anywhere in on-chain data — it existed only in the client's memory at tx time.
        # Therefore slippage_actual = (quoted_price - executed_price) / quoted_price
        # is structurally impossible to compute from historical RPC reconstruction.
        # This 0.01 is a fixed historical training default, NOT a fallback that could
        # ever be filled in at this code path. See GAP-6 audit (2026-07-31).
        slippage_actual = 0.01

        return {
            "position_size_usd": float(entry_value),
            "token_age_minutes": float(token_age_minutes),
            "liquidity_pool_depth": float(liquidity),
            "slippage_actual": float(slippage_actual),
            "cluster_score": float(cluster_score),
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
        from app.ml_pipeline.training_utils import stratified_train_test_split
        
        X_train, X_val, y_train, y_val = stratified_train_test_split(
            features.to_numpy(), labels, test_size=0.20, random_state=42
        )
        
        dtrain = xgb.DMatrix(X_train, label=y_train, weight=compute_class_sample_weights(y_train, num_class=3))
        dval = xgb.DMatrix(X_val, label=y_val)
        
        params = {
            "max_depth": 6,
            "learning_rate": 0.05,
            "objective": "multi:softprob",
            "num_class": 3,
            "seed": 42,
            "tree_method": "hist",
        }
        evals_list = [(dval, "val")] if len(X_val) > 0 else [(dtrain, "train")]
        model = xgb.train(
            params,
            dtrain,
            num_boost_round=300,
            evals=evals_list,
            verbose_eval=False
        )
        model.save_model(os.path.join(models_dir, "v0.json"))

    def _training_accuracy(self, features: pd.DataFrame, labels: np.ndarray, models_dir: str) -> float:
        from app.ml_pipeline.training_utils import stratified_train_test_split
        X_train, X_val, y_train, y_val = stratified_train_test_split(
            features.to_numpy(), labels, test_size=0.20, random_state=42
        )
        if len(y_val) == 0:
            X_val, y_val = features.to_numpy(), labels

        model = xgb.Booster()
        model.load_model(os.path.join(models_dir, "v0.json"))
        preds = np.argmax(model.predict(xgb.DMatrix(X_val)), axis=1)
        return float(np.sum(preds == y_val) / len(y_val)) if len(y_val) > 0 else 1.0

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
