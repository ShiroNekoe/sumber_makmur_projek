import asyncio
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, AsyncGenerator, Any

import numpy as np
import pandas as pd
import websockets
import xgboost as xgb

from app.core.config import settings
from app.domain.interfaces import (
    ITradeHistoryRepository,
    ITokenInfoService,
    IModelRegistryRepository,
)
from app.domain.models import ClosedTrade, ModelRegistry
from app.ml_pipeline.bootstrap import (
    FEATURE_COLUMNS,
    SolanaRpcHistoricalTransactionSource,
    TokenMarketSnapshot,
)

logger = logging.getLogger(__name__)


class NewTokenDiscoveryService:
    """
    Service for discovering newly launched Solana token pools in real-time via WebSocket 
    `logsSubscribe` filtering DEX creation logs, extracting candidate token mints, 
    verifying market depth & age, extracting 12 feature columns, and scoring with XGBoost Model v0.
    """

    WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
    STABLECOIN_MINTS = {
        "So11111111111111111111111111111111111111112",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    }

    # Instruction log indicators for pool creation across supported DEXes
    POOL_CREATION_LOG_KEYWORDS = [
        "instruction: initialize2",
        "instruction: initialize",
        "instruction: create",
        "instruction: initializepool",
        "instruction: openposition",
        "instruction: init",
        "instruction: initializemarket",
    ]

    def __init__(
        self,
        trade_history_repo: Optional[ITradeHistoryRepository] = None,
        token_info_service: Optional[ITokenInfoService] = None,
        model_registry_repo: Optional[IModelRegistryRepository] = None,
        rpc_url: Optional[str] = None,
        models_dir: str = "models",
        safety_check_gate: Optional[Any] = None,
    ):
        self.trade_history_repo = trade_history_repo
        self.token_info_service = token_info_service
        self.model_registry_repo = model_registry_repo
        self.rpc_url = rpc_url or settings.SOLANA_RPC_URL
        self.models_dir = models_dir
        self.safety_check_gate = safety_check_gate

        self.model: Optional[xgb.Booster] = None
        self.rpc_source = SolanaRpcHistoricalTransactionSource(rpc_url=self.rpc_url)
        self.candidate_queue: asyncio.Queue[str] = asyncio.Queue()

        # SOL/USD Price sliding buffer: List of (timestamp, price_usd)
        self._sol_price_history: List[Tuple[datetime, float]] = []
        self._last_sol_price_fetch: Optional[datetime] = None

        # Monitored DEX router program IDs from configuration
        self.dex_routers = getattr(
            settings,
            "RELEVANCE_FILTER_DEX_ROUTERS",
            [
                "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM V4
                "6EF8rrecMDMKMzBkv7jVLFv1E2syLQH5SH3iFh9FEAKB",  # pump.fun
                "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
                "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",   # Jupiter V6
                "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",  # Orca AMM V1
                "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX",   # Serum DEX V3
            ],
        )

    # ------------------------------------------------------------------
    # RPC WebSocket Conversion & Fallback Chain
    # ------------------------------------------------------------------
    def _get_wss_fallback_chain(self) -> List[str]:
        """
        Builds list of WSS URLs converted from HTTP RPC endpoints in fallback chain.
        """
        http_urls = [
            self.rpc_url,
            getattr(settings, "SOLANA_RPC_FALLBACK_URL", "https://api.mainnet-beta.solana.com"),
            getattr(settings, "RPC_SECONDARY_URL", "https://api.mainnet-beta.solana.com"),
            "https://api.mainnet-beta.solana.com",
        ]
        wss_urls = []
        for url in http_urls:
            if not url:
                continue
            if url.startswith("https://"):
                wss_url = "wss://" + url[8:]
            elif url.startswith("http://"):
                wss_url = "ws://" + url[7:]
            else:
                wss_url = url
            if wss_url not in wss_urls:
                wss_urls.append(wss_url)
        return wss_urls

    # ------------------------------------------------------------------
    # Task 1 — Live pool detection via logsSubscribe
    # ------------------------------------------------------------------
    async def _fetch_candidate_pairs_via_logs_subscribe(self) -> AsyncGenerator[str, None]:
        """
        Connects persistently to Solana RPC WebSocket endpoint, subscribes to `logsSubscribe`
        for monitored DEX router program IDs, filters for pool creation instructions, 
        fetches transaction details, and yields candidate token mint addresses.
        Includes automatic reconnection with exponential backoff & RPC fallback chain.
        """
        wss_chain = self._get_wss_fallback_chain()
        active_chain_idx = 0

        while True:
            active_wss_url = wss_chain[active_chain_idx]
            backoff = 2.0
            logger.info("[DISCOVERY] Connecting to Solana RPC WebSocket: %s", active_wss_url)

            try:
                async with websockets.connect(
                    active_wss_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                ) as ws:
                    logger.info("[DISCOVERY] Subscribing logsSubscribe for %d DEX routers...", len(self.dex_routers))
                    
                    # Subscribe to logs for each DEX router program ID
                    for idx, router_id in enumerate(self.dex_routers, start=1):
                        sub_request = {
                            "jsonrpc": "2.0",
                            "id": idx,
                            "method": "logsSubscribe",
                            "params": [
                                {"mentions": [router_id]},
                                {"commitment": "confirmed"},
                            ],
                        }
                        await ws.send(json.dumps(sub_request))

                    # Reset backoff on successful connection & subscription
                    backoff = 2.0

                    async for message in ws:
                        try:
                            data = json.loads(message)
                        except Exception:
                            continue

                        # Check if message is a logs notification
                        if data.get("method") != "logsNotification":
                            continue

                        params = data.get("params") or {}
                        result = params.get("result") or {}
                        value = result.get("value") or {}

                        # Skip failed transactions
                        if value.get("err") is not None:
                            continue

                        logs = value.get("logs") or []
                        signature = value.get("signature")

                        if not signature or not logs:
                            continue

                        # Check if any log line matches pool creation instruction keywords
                        is_pool_creation = False
                        for log_line in logs:
                            log_lower = log_line.lower()
                            if any(kw in log_lower for kw in self.POOL_CREATION_LOG_KEYWORDS):
                                is_pool_creation = True
                                break

                        if not is_pool_creation:
                            continue

                        logger.info("[DISCOVERY] Detected pool creation log! Signature: %s...", signature[:16])

                        # Fetch full transaction to extract new token mint (with retry for indexing delay)
                        try:
                            tx = None
                            for retry_tx in range(3):
                                tx = await self.rpc_source._fetch_transaction(signature)
                                if tx:
                                    break
                                await asyncio.sleep(0.4)

                            if not tx:
                                logger.warning("[DISCOVERY] Transaction %s not yet indexed on RPC.", signature[:16])
                                continue

                            mint_addresses = self._extract_mints_from_transaction(tx)
                            for mint in mint_addresses:
                                logger.info("[DISCOVERY] Extracted new candidate token mint: %s", mint)
                                yield mint

                        except Exception as tx_err:
                            logger.warning("[DISCOVERY] Failed to parse transaction %s: %s", signature[:16], tx_err)

            except (websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as ws_err:
                logger.warning(
                    "[DISCOVERY] WebSocket connection lost on %s: %s. Retrying in %.1fs...",
                    active_wss_url,
                    ws_err,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 32.0)

                # Rotate to next RPC fallback endpoint if persistent failure occurs
                if active_chain_idx + 1 < len(wss_chain):
                    active_chain_idx += 1
                    logger.warning("[DISCOVERY] Switching RPC WSS endpoint to fallback: %s", wss_chain[active_chain_idx])
                else:
                    active_chain_idx = 0

            except Exception as unk_err:
                logger.error("[DISCOVERY] Unexpected error in WebSocket stream: %s. Reconnecting...", unk_err)
                await asyncio.sleep(backoff)

    def _extract_mints_from_transaction(self, tx: dict) -> List[str]:
        """
        Extracts non-stablecoin/non-SOL token mint addresses from parsed transaction token balances.
        """
        mints: List[str] = []
        meta = tx.get("meta") or {}
        post_balances = meta.get("postTokenBalances") or []
        pre_balances = meta.get("preTokenBalances") or []

        for bal in post_balances + pre_balances:
            mint = bal.get("mint")
            if mint and mint not in self.STABLECOIN_MINTS and mint not in mints:
                mints.append(mint)
        return mints

    async def _fetch_candidate_pairs(self) -> List[str]:
        """
        Fetches pending candidate token mints from the candidate queue populated by WebSocket stream.
        """
        candidates: List[str] = []
        while not self.candidate_queue.empty():
            try:
                mint = self.candidate_queue.get_nowait()
                candidates.append(mint)
            except asyncio.QueueEmpty:
                break
        return candidates

    # ------------------------------------------------------------------
    # Pair Snapshot & Hard Filters
    # ------------------------------------------------------------------
    async def _fetch_pair_snapshot(self, token_mint: str) -> Optional[TokenMarketSnapshot]:
        """
        Fetches DexScreener snapshot for liquidity and age verification.
        (Existing signature & responsibilities preserved).
        """
        def sync_fetch() -> Optional[TokenMarketSnapshot]:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                payload = json.loads(r.read().decode("utf-8"))
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
            return await asyncio.to_thread(sync_fetch)
        except Exception as e:
            logger.warning("[DISCOVERY] Could not fetch pair snapshot for %s: %s", token_mint, e)
            return None

    def _passes_filters(self, snapshot: TokenMarketSnapshot) -> bool:
        """
        Validates minimum liquidity depth and token age thresholds from config.yaml.
        """
        if not snapshot:
            return False

        min_liquidity = getattr(settings, "MIN_LIQUIDITY_USD", 3000.0)
        if snapshot.liquidity_usd < min_liquidity:
            logger.info("[DISCOVERY] Filter rejected: liquidity $%.2f < $%.2f", snapshot.liquidity_usd, min_liquidity)
            return False

        if not snapshot.pair_created_at:
            logger.info("[DISCOVERY] Filter rejected: token pair_created_at is missing or unverifiable")
            return False

        age_minutes = (datetime.now(timezone.utc) - snapshot.pair_created_at).total_seconds() / 60.0
        min_age = getattr(settings, "MIN_TOKEN_AGE_MINUTES", 2.0)
        max_age = getattr(settings, "MAX_TOKEN_AGE_MINUTES", 30.0)

        if not (min_age <= age_minutes <= max_age):
            logger.info("[DISCOVERY] Filter rejected: age %.1f mins outside [%.1f, %.1f]", age_minutes, min_age, max_age)
            return False

        return True

    # ------------------------------------------------------------------
    # Task 2 — Wire wallet-stats service for 5 placeholder features
    # ------------------------------------------------------------------
    async def _update_sol_price_cache(self) -> None:
        """
        Updates SOL/USD price history sliding buffer for real-time momentum calculation.
        """
        now = datetime.now(timezone.utc)
        if self._last_sol_price_fetch and (now - self._last_sol_price_fetch).total_seconds() < 60.0:
            return

        def sync_fetch_sol_price() -> float:
            url = "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                payload = json.loads(r.read().decode("utf-8"))
            pairs = payload.get("pairs") or []
            if pairs:
                return float(pairs[0].get("priceUsd") or 0.0)
            return 0.0

        try:
            sol_price = await asyncio.to_thread(sync_fetch_sol_price)
            if sol_price > 0:
                self._sol_price_history.append((now, sol_price))
                self._last_sol_price_fetch = now

                # Prune records older than 15 minutes
                cutoff = now - timedelta(minutes=15)
                self._sol_price_history = [(ts, p) for ts, p in self._sol_price_history if ts >= cutoff]
        except Exception as e:
            logger.debug("[DISCOVERY] Could not update live SOL price for momentum: %s", e)

    def _calculate_sol_usd_momentum(self) -> float:
        """
        Calculates 5-minute SOL/USD price percentage change (momentum).
        """
        if len(self._sol_price_history) < 2:
            return 0.0

        now = datetime.now(timezone.utc)
        current_price = self._sol_price_history[-1][1]

        # Find price closest to 5 minutes ago
        target_time = now - timedelta(minutes=5)
        closest_entry = min(self._sol_price_history, key=lambda x: abs((x[0] - target_time).total_seconds()))
        past_price = closest_entry[1]

        if past_price <= 0:
            return 0.0

        return float((current_price - past_price) / past_price)

    async def _extract_features(self, token_mint: str, snapshot: TokenMarketSnapshot) -> np.ndarray:
        """
        Extracts 12 feature columns matching FEATURE_COLUMNS order:
        [position_size_usd, token_age_minutes, liquidity_pool_depth, slippage_actual,
         cluster_score, win_rate_30d, avg_holding_time_minutes, typical_trade_size_usd,
         past_exit_pattern_score, sol_usd_momentum, token_volume_liquidity_ratio, hour_of_day_utc]
        """
        now = datetime.now(timezone.utc)

        # 1. On-chain token metrics from snapshot
        position_size_usd = 500.0  # Default evaluation position size
        token_age_minutes = 60.0
        if snapshot.pair_created_at:
            token_age_minutes = max(0.0, (now - snapshot.pair_created_at).total_seconds() / 60.0)

        liquidity_pool_depth = max(0.0, snapshot.liquidity_usd)
        logger.warning(
            f"[SLIPPAGE] Actual execution slippage not available in discovery phase for token {token_mint[:8]}... "
            f"Falling back to default slippage_actual 0.01 (1%)."
        )
        slippage_actual = 0.01

        from app.domain.cluster_logic import compute_cluster_score
        from app.use_cases.dashboard_query import get_all_signal_events
        recent_events = get_all_signal_events()
        cluster_score = compute_cluster_score(
            target_wallet="DISCOVERY_ENGINE",
            target_token=token_mint,
            target_timestamp=now,
            events=recent_events,
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        # 2. Query historical wallet trade stats over rolling 30-day window
        win_rate_30d = 0.45
        avg_holding_time_minutes = 20.0
        typical_trade_size_usd = 500.0
        past_exit_pattern_score = 0.0

        if self.trade_history_repo:
            try:
                rolling_days = getattr(settings, "RETRAIN_ROLLING_WINDOW_DAYS", 30)
                thirty_days_ago = now - timedelta(days=rolling_days)

                closed_trades: List[ClosedTrade] = await self.trade_history_repo.get_closed_trades(limit=200)
                recent_trades = [
                    t for t in closed_trades
                    if t.exit_ts is not None and (t.exit_ts.replace(tzinfo=timezone.utc) if t.exit_ts.tzinfo is None else t.exit_ts) > thirty_days_ago
                ]

                if recent_trades:
                    total_trades = len(recent_trades)
                    win_count = sum(1 for t in recent_trades if t.label == "BUY_BENAR")
                    win_rate_30d = max(0.0, min(float(win_count) / total_trades, 1.0))

                    avg_holding_time_minutes = float(sum(t.holding_time_minutes for t in recent_trades)) / total_trades
                    typical_trade_size_usd = float(sum(t.position_size_usd for t in recent_trades)) / total_trades

                    kill_exits = sum(1 for t in recent_trades if t.exit_reason and t.exit_reason.startswith("kill_switch"))
                    past_exit_pattern_score = float(kill_exits) / total_trades

            except Exception as repo_err:
                logger.error("[DISCOVERY] Error querying SQLite historical trade stats: %s", repo_err)

        # 3. Market context: SOL/USD momentum & volume-liquidity ratio
        await self._update_sol_price_cache()
        sol_usd_momentum = self._calculate_sol_usd_momentum()

        volume_ratio = snapshot.volume_24h / liquidity_pool_depth if liquidity_pool_depth > 0 else 0.0
        hour_of_day_utc = float(now.hour)

        # Assemble row matching FEATURE_COLUMNS order
        feature_dict = {
            "position_size_usd": position_size_usd,
            "token_age_minutes": token_age_minutes,
            "liquidity_pool_depth": liquidity_pool_depth,
            "slippage_actual": slippage_actual,
            "cluster_score": cluster_score,
            "win_rate_30d": win_rate_30d,
            "avg_holding_time_minutes": avg_holding_time_minutes,
            "typical_trade_size_usd": typical_trade_size_usd,
            "past_exit_pattern_score": past_exit_pattern_score,
            "sol_usd_momentum": sol_usd_momentum,
            "token_volume_liquidity_ratio": volume_ratio,
            "hour_of_day_utc": hour_of_day_utc,
        }

        df = pd.DataFrame([feature_dict], columns=FEATURE_COLUMNS)
        return df.to_numpy()

    # ------------------------------------------------------------------
    # Model Loading & Scoring
    # ------------------------------------------------------------------
    async def _ensure_model_loaded(self) -> None:
        """
        Ensures XGBoost Booster model is loaded into memory from models_dir/v0.json.
        """
        if self.model is not None:
            return

        import os
        model_path = os.path.join(self.models_dir, "v0.json")
        if os.path.exists(model_path):
            try:
                booster = xgb.Booster()
                booster.load_model(model_path)
                self.model = booster
                logger.info("[DISCOVERY] Loaded XGBoost model from %s", model_path)
            except Exception as e:
                logger.error("[DISCOVERY] Error loading model from %s: %s", model_path, e)

    async def _score(self, feature_row: np.ndarray) -> float:
        """
        Runs XGBoost multi-class prediction and returns probability for Class 1 (BUY_BENAR).
        """
        await self._ensure_model_loaded()
        if not self.model:
            logger.warning("[DISCOVERY] Model not loaded. Returning default score 0.0")
            return 0.0

        try:
            dmatrix = xgb.DMatrix(feature_row, feature_names=FEATURE_COLUMNS)
            preds = self.model.predict(dmatrix)
            # preds shape: (1, 3) where index 1 = BUY_BENAR
            if len(preds.shape) > 1 and preds.shape[1] >= 2:
                buy_benar_prob = float(preds[0][1])
                return buy_benar_prob
            return float(preds[0])
        except Exception as e:
            logger.error("[DISCOVERY] Inference failed: %s", e)
            return 0.0

    # ------------------------------------------------------------------
    # Main Operations: scan_once() & run_forever()
    # ------------------------------------------------------------------
    async def scan_once(self) -> List[Dict[str, Any]]:
        """
        Scans pending candidate tokens in queue, verifies filters, extracts features, and scores them.
        """
        discovered_opportunities: List[Dict[str, Any]] = []
        candidates = await self._fetch_candidate_pairs()

        for mint in candidates:
            snapshot = await self._fetch_pair_snapshot(mint)
            if not snapshot:
                continue

            if not self._passes_filters(snapshot):
                continue

            features = await self._extract_features(mint, snapshot)
            score = await self._score(features)

            confidence_threshold = getattr(settings, "CONFIDENCE_THRESHOLD", 0.50)
            logger.info("[DISCOVERY] Candidate %s | Score: %.4f | Threshold: %.2f", mint[:12], score, confidence_threshold)

            if score >= confidence_threshold:
                opp = {
                    "token_mint": mint,
                    "score": score,
                    "price_usd": snapshot.price_usd,
                    "liquidity_usd": snapshot.liquidity_usd,
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                }
                discovered_opportunities.append(opp)

                # Active Execution Trigger!
                if getattr(settings, "DISCOVERY_TRADE_ENABLED", True) and self.safety_check_gate:
                    try:
                        from app.domain.models import FeatureVector, PredictionResult
                        
                        now = datetime.now(timezone.utc)
                        token_age = 60.0
                        if snapshot.pair_created_at:
                            token_age = max(0.0, (now - snapshot.pair_created_at).total_seconds() / 60.0)
                            
                        # Query sqlite stats (extracted from our _extract_features code)
                        win_rate_30d = 0.45
                        avg_holding_time_minutes = 20.0
                        typical_trade_size_usd = 500.0
                        past_exit_pattern_score = 0.0
                        
                        if self.trade_history_repo:
                            try:
                                rolling_days = getattr(settings, "RETRAIN_ROLLING_WINDOW_DAYS", 30)
                                thirty_days_ago = now - timedelta(days=rolling_days)
                                closed_trades = await self.trade_history_repo.get_closed_trades(limit=200)
                                recent_trades = [
                                    t for t in closed_trades
                                    if t.exit_ts is not None and (t.exit_ts.replace(tzinfo=timezone.utc) if t.exit_ts.tzinfo is None else t.exit_ts) > thirty_days_ago
                                ]
                                if recent_trades:
                                    total_trades = len(recent_trades)
                                    win_count = sum(1 for t in recent_trades if t.label == "BUY_BENAR")
                                    win_rate_30d = max(0.0, min(float(win_count) / total_trades, 1.0))
                                    avg_holding_time_minutes = float(sum(t.holding_time_minutes for t in recent_trades)) / total_trades
                                    typical_trade_size_usd = float(sum(t.position_size_usd for t in recent_trades)) / total_trades
                                    kill_exits = sum(1 for t in recent_trades if t.exit_reason and t.exit_reason.startswith("kill_switch"))
                                    past_exit_pattern_score = float(kill_exits) / total_trades
                            except Exception:
                                pass
                                
                        sol_usd_momentum = self._calculate_sol_usd_momentum()
                        volume_ratio = snapshot.volume_24h / snapshot.liquidity_usd if snapshot.liquidity_usd > 0 else 0.0
                        
                        fv = FeatureVector(
                            token_address=mint,
                            wallet_source="new_token_discovery",
                            signature="discovery_sig_" + mint[:8],
                            timestamp=now,
                            position_size_usd=500.0,
                            token_age_minutes=token_age,
                            liquidity_pool_depth=max(0.0, snapshot.liquidity_usd),
                            slippage_actual=0.01,
                            cluster_score=1.0,
                            win_rate_30d=win_rate_30d,
                            avg_holding_time_minutes=avg_holding_time_minutes,
                            typical_trade_size_usd=typical_trade_size_usd,
                            past_exit_pattern_score=past_exit_pattern_score,
                            sol_usd_momentum=sol_usd_momentum,
                            token_volume_liquidity_ratio=volume_ratio,
                            hour_of_day_utc=now.hour
                        )
                        
                        pred_result = PredictionResult(
                            direction="BUY",
                            confidence_score=score,
                            target_price_estimate=0.05, # default 5% target offset
                            token_address=mint,
                            wallet_source="new_token_discovery",
                            signature=fv.signature,
                            timestamp=now,
                            cooldown_already_cleared=False
                        )
                        
                        logger.info(f"[DISCOVERY] [ACTIVE TRADE] Triggering active trade evaluation for {mint}...")
                        # Run safety checks which will trigger AutoTradeExecutor automatically if passed
                        asyncio.create_task(self.safety_check_gate.evaluate_safety(pred_result, fv))
                    except Exception as exec_err:
                        logger.error(f"[DISCOVERY] [ACTIVE TRADE] Error triggering active trade flow: {exec_err}", exc_info=True)

        return discovered_opportunities

    async def run_forever(self, interval_seconds: float = 10.0) -> None:
        """
        Main async daemon loop that starts WebSocket live listener task and processes
        discovered pool creation candidates continuously.
        """
        logger.info("[DISCOVERY] Launching NewTokenDiscoveryService run_forever daemon...")

        # Worker task to stream live pool creations into queue
        async def websocket_worker():
            async for mint in self._fetch_candidate_pairs_via_logs_subscribe():
                await self.candidate_queue.put(mint)

        ws_task = asyncio.create_task(websocket_worker())

        try:
            while True:
                opportunities = await self.scan_once()
                if opportunities:
                    for opp in opportunities:
                        logger.info(
                            "[DISCOVERY] HIGH CONFIDENCE TOKEN FOUND: %s | Score: %.4f | Liq: $%.2f",
                            opp["token_mint"],
                            opp["score"],
                            opp["liquidity_usd"],
                        )
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("[DISCOVERY] Stopping run_forever daemon...")
            ws_task.cancel()
            await asyncio.gather(ws_task, return_exceptions=True)
        except Exception as e:
            logger.error("[DISCOVERY] Fatal error in run_forever daemon: %s", e, exc_info=True)
            ws_task.cancel()
            await asyncio.gather(ws_task, return_exceptions=True)
            raise
