import logging
import time
from typing import Dict, List, Any
from app.domain.interfaces import IPositionRepository
from app.use_cases.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

cg_cache = {
    "1d": {"data": None, "updated_at": 0.0},
    "365d": {"data": None, "updated_at": 0.0}
}


class PnLCalculator:
    """
    F-07 / B2: PnL calculation service.
    Computes realized PnL from closed trades database logs,
    and unrealized PnL from on-chain holdings and purchase cost basis.
    Also manages equity snapshots for accurate portfolio value history.
    """
    def __init__(
        self,
        position_repo: IPositionRepository,
        trade_history_repo,
        portfolio_service: PortfolioService,
        db_session=None
    ):
        self.position_repo = position_repo
        self.trade_history_repo = trade_history_repo
        self.portfolio_service = portfolio_service
        self.db = db_session  # Optional SQLAlchemy session for equity snapshots

    async def calculate_realized_pnl(self) -> float:
        """
        Calculates total realized PnL in USD from REAL (non-bootstrap, non-paper) closed trades only.
        Bootstrap and paper trades are simulation data and must never contribute to portfolio PnL.
        """
        try:
            # Access the db session directly to avoid the per-call limit
            db = getattr(self.trade_history_repo, "db", None)
            if db:
                from app.infrastructure.database.models import ClosedTradeORM
                rows = db.query(
                    ClosedTradeORM.position_size_usd,
                    ClosedTradeORM.pnl_pct_actual
                ).filter(
                    (ClosedTradeORM.is_bootstrap == False) | (ClosedTradeORM.is_bootstrap == None),
                    (ClosedTradeORM.is_paper_trade == False) | (ClosedTradeORM.is_paper_trade == None)
                ).all()
                return sum(r.position_size_usd * r.pnl_pct_actual for r in rows)
            # Fallback: use interface (exclude bootstrap and paper trades)
            closed_trades = await self.trade_history_repo.get_closed_trades(limit=1000, exclude_bootstrap=True)
            return sum(t.position_size_usd * t.pnl_pct_actual for t in closed_trades if not t.is_paper_trade)
        except Exception as e:
            logger.error(f"[PNL CALCULATOR] Failed to calculate realized PnL: {e}")
            return 0.0


    async def calculate_unrealized_pnl(self, pubkey_str: str) -> Dict[str, Any]:
        """
        Calculates unrealized PnL in USD.
        Unrealized PnL = (Current Market Price - Entry Cost Basis) * Current Balance
        """
        try:
            holdings = await self.portfolio_service.get_token_holdings(pubkey_str)
            open_positions = await self.position_repo.get_open_positions()
            
            # Map open positions by token address for easy lookup
            pos_map = {pos.token_address: pos for pos in open_positions}
            
            total_unrealized = 0.0
            total_portfolio_value = 0.0
            holdings_pnl = []
            
            for hold in holdings:
                mint = hold["mint"]
                current_price = hold["price_usd"]
                amount = hold["amount"]
                value_usd = hold["value_usd"]
                total_portfolio_value += value_usd
                
                # Check if we have cost basis in open positions
                cost_basis = 0.0
                if mint in pos_map:
                    cost_basis = pos_map[mint].entry_price or 0.0
                
                unrealized_usd = 0.0
                unrealized_pct = 0.0
                
                if cost_basis > 0.0 and current_price > 0.0:
                    unrealized_usd = (current_price - cost_basis) * amount
                    unrealized_pct = (current_price - cost_basis) / cost_basis
                    
                total_unrealized += unrealized_usd
                
                holdings_pnl.append({
                    "mint": mint,
                    "symbol": hold["symbol"],
                    "name": hold["name"],
                    "amount": amount,
                    "price_usd": current_price,
                    "cost_basis": cost_basis,
                    "value_usd": value_usd,
                    "unrealized_pnl_usd": unrealized_usd,
                    "unrealized_pnl_pct": unrealized_pct
                })
                
            return {
                "total_unrealized_pnl_usd": total_unrealized,
                "total_portfolio_value_usd": total_portfolio_value,
                "holdings": holdings_pnl
            }
            
        except Exception as e:
            logger.error(f"[PNL CALCULATOR] Failed to calculate unrealized PnL: {e}", exc_info=True)
            return {
                "total_unrealized_pnl_usd": 0.0,
                "total_portfolio_value_usd": 0.0,
                "holdings": []
            }
            
    async def fetch_coingecko_prices(self, days: int) -> List[List[float]]:
        """
        Fetches historical prices from CoinGecko with in-memory caching.
        """
        now = time.time()
        cache_key = "1d" if days == 1 else "365d"
        cache_expiry = 600 if days == 1 else 3600 # 10 mins for 1d, 1 hour for 365d
        
        cache_entry = cg_cache.get(cache_key)
        if cache_entry and cache_entry["data"] is not None and (now - cache_entry["updated_at"] < cache_expiry):
            return cache_entry["data"]
            
        import urllib.request
        import json
        import asyncio
        
        url = f"https://api.coingecko.com/api/v3/coins/solana/market_chart?vs_currency=usd&days={days}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        
        try:
            def fetch_cg():
                with urllib.request.urlopen(req, timeout=4) as response:
                    return json.loads(response.read().decode("utf-8"))
            cg_data = await asyncio.to_thread(fetch_cg)
            if "prices" in cg_data:
                prices = cg_data["prices"]
                cg_cache[cache_key] = {
                    "data": prices,
                    "updated_at": now
                }
                return prices
        except Exception as e:
            logger.warning(f"[PNL CALCULATOR] Failed to fetch CoinGecko SOL prices for days={days}: {e}. Caching dummy price data.")
            dummy_prices = []
            end_ts = int(time.time() * 1000)
            start_ts = end_ts - (days * 24 * 3600 * 1000)
            step = 3600 * 1000 if days == 1 else 24 * 3600 * 1000
            for ts in range(start_ts, end_ts, step):
                dummy_prices.append([float(ts), 145.0])
            cg_cache[cache_key] = {
                "data": dummy_prices,
                "updated_at": now
            }
            return dummy_prices
        
        return cache_entry["data"] if cache_entry and cache_entry["data"] else []

    async def get_wallet_balance_changes(self, pubkey_str: str, sol_balance: float = 0.0) -> List[Dict[str, Any]]:
        """
        Fetches the wallet's SOL transaction signatures and balance changes.
        Uses 3-stage RPC fallback: QuickNode → Helius → Public.
        """
        import urllib.request
        import json
        import asyncio
        from app.core.config import settings

        # 3-stage RPC fallback chain
        rpc_chain = [
            settings.RPC_PRIMARY_URL,
            settings.RPC_SECONDARY_URL,
            "https://api.mainnet-beta.solana.com",
        ]

        def _post_rpc(url: str, body: bytes, timeout: int = 10):
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))

        # 1. Fetch signatures with fallback
        sig_payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [pubkey_str, {"limit": 4}]
        }).encode()

        signatures_list = []
        for rpc_url in rpc_chain:
            try:
                res = await asyncio.to_thread(_post_rpc, rpc_url, sig_payload, 5)
                if "result" in res:
                    signatures_list = res["result"]
                    break
            except Exception as e:
                logger.debug(f"[PNL CALCULATOR] RPC {rpc_url[:30]}... failed for sigs: {e}")
                continue

        if not signatures_list:
            logger.debug(f"[PNL CALCULATOR] All RPCs failed for getSignaturesForAddress {pubkey_str[:12]}...")
            return []

        successful_sigs = [x["signature"] for x in signatures_list if isinstance(x, dict) and x.get("err") is None][:4]
        if not successful_sigs:
            return []

        # 2. Batch get transactions with fallback
        batch_payload = json.dumps([
            {
                "jsonrpc": "2.0", "id": idx,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }
            for idx, sig in enumerate(successful_sigs)
        ]).encode()

        batch_res = None
        for rpc_url in rpc_chain:
            try:
                batch_res = await asyncio.to_thread(_post_rpc, rpc_url, batch_payload, 12)
                if isinstance(batch_res, list):
                    break
            except Exception as e:
                logger.debug(f"[PNL CALCULATOR] RPC {rpc_url[:30]}... failed for batch tx: {e}")
                continue

        tx_changes = []
        if isinstance(batch_res, list):
            for item in batch_res:
                tx = item.get("result") if isinstance(item, dict) else None
                if not tx:
                    continue
                block_time = tx.get("blockTime")
                if block_time is None:
                    continue
                meta = tx.get("meta", {})
                pre_bals = meta.get("preBalances", [])
                post_bals = meta.get("postBalances", [])
                try:
                    acc_keys_data = tx["transaction"]["message"]["accountKeys"]
                    account_keys = [x["pubkey"] if isinstance(x, dict) else x for x in acc_keys_data]
                    wallet_idx = account_keys.index(pubkey_str)
                    pre_bal = pre_bals[wallet_idx]
                    post_bal = post_bals[wallet_idx]
                    change_sol = (post_bal - pre_bal) / 1_000_000_000.0
                    tx_changes.append({"timestamp": block_time, "change_sol": change_sol})
                except (ValueError, IndexError, KeyError):
                    continue

        tx_changes.sort(key=lambda x: x["timestamp"], reverse=True)
        return tx_changes


    async def reconstruct_timeframe_history(
        self,
        pubkey_str: str,
        current_value: float,
        sol_balance: float,
        cg_prices: List[List[float]],
        days: int,
        interval_hours: float,
        tx_changes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Reconstructs the portfolio value history backwards from current time.
        
        IMPORTANT: Portfolio equity only changes when:
        1. There's a transaction (SOL in/out)
        2. Token holdings change (buy/sell)
        3. Open positions are realized (exit)
        
        It does NOT follow SOL price history - we use current SOL price for all time points
        to show actual equity movements, not synthetic price tracking.
        """
        import math
        import random
        from datetime import datetime, timezone, timedelta
        import uuid

        history = []
        now = datetime.now(timezone.utc)
        
        # Get current SOL price for all historical calculations
        current_sol_price = 77.34
        try:
            holdings = await self.portfolio_service.get_token_holdings(pubkey_str)
            sol_holding = next((h for h in holdings if h["symbol"] == "SOL"), None)
            if sol_holding:
                current_sol_price = sol_holding["price_usd"]
        except Exception:
            pass

        # Load closed trades and open positions to reconstruct true equity
        try:
            closed_trades = await self.trade_history_repo.get_closed_trades(exclude_bootstrap=False)
            open_positions = await self.position_repo.get_open_positions()
        except Exception as e:
            logger.error(f"[PNL CALCULATOR] Failed to load trades/positions for history: {e}")
            closed_trades = []
            open_positions = []

        # Parse prices into buckets (for reference, but we won't use historical prices for portfolio value)
        N = int((days * 24) / interval_hours)

        for idx in range(N):
            target_time = now - timedelta(hours=int((N - 1 - idx) * interval_hours))
            target_ts_sec = int(target_time.timestamp())
            
            # Trace actual SOL balance at target_time backwards from transaction history
            sol_bal_at_t = sol_balance
            for tx in tx_changes:
                if tx["timestamp"] > target_ts_sec:
                    sol_bal_at_t -= tx["change_sol"]
            sol_bal_at_t = max(0.0, sol_bal_at_t)
            
            # Reconstruct active token holdings and PnL at target_time
            token_val_at_t = 0.0
            realized_pnl_at_t = 0.0
            unrealized_pnl_at_t = 0.0
            
            for trade in closed_trades:
                entry_ts = trade.entry_ts.replace(tzinfo=timezone.utc) if trade.entry_ts.tzinfo is None else trade.entry_ts
                exit_ts = trade.exit_ts.replace(tzinfo=timezone.utc) if trade.exit_ts.tzinfo is None else trade.exit_ts
                
                if entry_ts <= target_time < exit_ts:
                    # Token was held during target_time, interpolate its valuation PnL
                    total_dur = (exit_ts - entry_ts).total_seconds()
                    elapsed = (target_time - entry_ts).total_seconds()
                    pnl_pct = trade.pnl_pct_actual * (elapsed / total_dur) if total_dur > 0 else 0.0
                    token_val_at_t += trade.position_size_usd * (1.0 + pnl_pct)
                    unrealized_pnl_at_t += trade.position_size_usd * pnl_pct
                elif exit_ts <= target_time:
                    # Trade closed before target_time, add to realized PnL
                    realized_pnl_at_t += trade.position_size_usd * trade.pnl_pct_actual
            
            for pos in open_positions:
                entry_ts = pos.entry_ts.replace(tzinfo=timezone.utc) if pos.entry_ts.tzinfo is None else pos.entry_ts
                if entry_ts <= target_time:
                    # Position is currently open and active at target_time, value it at cost basis
                    token_val_at_t += pos.position_size_usd
            
            # ┌─────────────────────────────────────────────────────────────────┐
            # │ KEY FIX: Use CURRENT SOL price for all time points              │
            # │ This way, portfolio value only changes when:                    │
            # │ - SOL balance changes (transaction)                             │
            # │ - Token holdings change (trade entry/exit)                      │
            # │ - Not due to SOL price fluctuations                             │
            # └─────────────────────────────────────────────────────────────────┘
            
            if sol_balance > 0.0:
                # Real wallet: calculate equity using CURRENT SOL price (not historical price)
                # This ensures portfolio value only reflects actual equity changes, not price movements
                val_at_t = sol_bal_at_t * current_sol_price + token_val_at_t
                sol_balance_diff_usd = (sol_bal_at_t - sol_balance) * current_sol_price
                pnl_at_t = sol_balance_diff_usd + realized_pnl_at_t + unrealized_pnl_at_t
            else:
                # Paper trading: $10,000 starting cash baseline + cumulative trade PnL
                pnl_at_t = realized_pnl_at_t + unrealized_pnl_at_t
                val_at_t = 10000.0 + pnl_at_t

            history.append({
                "timestamp": target_time.isoformat(),
                "value_usd": round(val_at_t, 2),
                "pnl_usd": round(pnl_at_t, 2),
                "sol_balance": round(sol_bal_at_t, 4)
            })
            
        return history

    async def get_portfolio_summary(self, pubkey_str: str) -> Dict[str, Any]:
        """
        Aggregates realized, unrealized PnL and total values, including multiple histories.
        
        PRIORITY:
        1. Use real equity snapshots from database (if available)
        2. Only fallback to reconstruction if insufficient historical data
        3. Append current state as latest snapshot
        """
        realized = await self.calculate_realized_pnl()
        unrealized_data = await self.calculate_unrealized_pnl(pubkey_str)
        
        holdings = unrealized_data["holdings"]
        sol_holding = next((h for h in holdings if h["symbol"] == "SOL"), None)
        sol_balance = sol_holding["amount"] if sol_holding else 0.0
        sol_price = sol_holding["price_usd"] if sol_holding else 77.34
        
        current_value = unrealized_data["total_portfolio_value_usd"]
        
        # Try to get real equity snapshots from database first
        history_1d = await self._get_equity_snapshots_for_timeframe(pubkey_str, days=1, current_value=current_value, sol_balance=sol_balance, sol_price=sol_price, realized_pnl=realized, unrealized_pnl=unrealized_data["total_unrealized_pnl_usd"])
        history_7d = await self._get_equity_snapshots_for_timeframe(pubkey_str, days=7, current_value=current_value, sol_balance=sol_balance, sol_price=sol_price, realized_pnl=realized, unrealized_pnl=unrealized_data["total_unrealized_pnl_usd"])
        history_30d = await self._get_equity_snapshots_for_timeframe(pubkey_str, days=30, current_value=current_value, sol_balance=sol_balance, sol_price=sol_price, realized_pnl=realized, unrealized_pnl=unrealized_data["total_unrealized_pnl_usd"])
        history_180d = await self._get_equity_snapshots_for_timeframe(pubkey_str, days=180, current_value=current_value, sol_balance=sol_balance, sol_price=sol_price, realized_pnl=realized, unrealized_pnl=unrealized_data["total_unrealized_pnl_usd"])
        history_360d = await self._get_equity_snapshots_for_timeframe(pubkey_str, days=365, current_value=current_value, sol_balance=sol_balance, sol_price=sol_price, realized_pnl=realized, unrealized_pnl=unrealized_data["total_unrealized_pnl_usd"])
        
        return {
            "realized_pnl_usd": realized,
            "unrealized_pnl_usd": unrealized_data["total_unrealized_pnl_usd"],
            "total_pnl_usd": realized + unrealized_data["total_unrealized_pnl_usd"],
            "portfolio_value_usd": current_value,
            "holdings": holdings,
            "history_1d": history_1d,
            "history_7d": history_7d,
            "history_30d": history_30d,
            "history_180d": history_180d,
            "history_360d": history_360d
        }

    async def _get_equity_snapshots_for_timeframe(
        self,
        wallet_address: str,
        days: int,
        current_value: float,
        sol_balance: float,
        sol_price: float,
        realized_pnl: float,
        unrealized_pnl: float
    ) -> List[Dict[str, Any]]:
        """
        Retrieves REAL equity snapshots from database for given timeframe.
        
        If no snapshots exist:
        - Returns just current state as single point
        
        If snapshots exist but don't cover full timeframe:
        - Appends current state as latest point
        - Does NOT synthesize missing historical data
        
        This ensures graph shows ACTUAL portfolio changes, not reconstructed/synthetic data.
        """
        from datetime import datetime, timezone, timedelta
        
        if not self.db:
            # Fallback: return only current state
            return [{
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "value_usd": round(current_value, 2),
                "pnl_usd": round(realized_pnl + unrealized_pnl, 2),
                "sol_balance": round(sol_balance, 4)
            }]
        
        try:
            from app.infrastructure.database.models import EquitySnapshotORM
            
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Query REAL snapshots from database
            snapshots = self.db.query(EquitySnapshotORM).filter(
                EquitySnapshotORM.wallet_address == wallet_address,
                EquitySnapshotORM.timestamp >= cutoff_time
            ).order_by(EquitySnapshotORM.timestamp.asc()).all()
            
            history = []
            
            if snapshots:
                # Use real snapshots
                for snap in snapshots:
                    # Ensure timestamp is timezone-aware for consistent comparison
                    snap_time = snap.timestamp
                    if snap_time.tzinfo is None:
                        snap_time = snap_time.replace(tzinfo=timezone.utc)
                    
                    history.append({
                        "timestamp": snap_time.isoformat(),
                        "value_usd": snap.portfolio_value_usd,
                        "pnl_usd": snap.total_pnl_usd,
                        "sol_balance": round(snap.sol_balance, 4)
                    })
                
                # Check if we need to add current state as latest
                # (in case it's newer than last snapshot)
                if history:
                    last_snap_str = history[-1]["timestamp"]
                    # Parse ISO format timestamp safely
                    if '+' in last_snap_str:
                        last_snap_time = datetime.fromisoformat(last_snap_str)
                    else:
                        # Naive timestamp, add UTC timezone
                        last_snap_time = datetime.fromisoformat(last_snap_str).replace(tzinfo=timezone.utc)
                    
                    now = datetime.now(timezone.utc)
                    
                    # If current state is significantly different or time has passed, add it
                    if (now - last_snap_time).total_seconds() > 300 or abs(history[-1]["value_usd"] - current_value) > 0.01:
                        history.append({
                            "timestamp": now.isoformat(),
                            "value_usd": round(current_value, 2),
                            "pnl_usd": round(realized_pnl + unrealized_pnl, 2),
                            "sol_balance": round(sol_balance, 4)
                        })
                
                logger.info(f"[PNL CALCULATOR] Using {len(snapshots)} real equity snapshots for {wallet_address[:8]}... (days={days})")
                return history
            else:
                # No snapshots exist - return only current state
                logger.info(f"[PNL CALCULATOR] No equity snapshots found for {wallet_address[:8]}... Return current state only")
                return [{
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "value_usd": round(current_value, 2),
                    "pnl_usd": round(realized_pnl + unrealized_pnl, 2),
                    "sol_balance": round(sol_balance, 4)
                }]
                
        except Exception as e:
            logger.error(f"[PNL CALCULATOR] Failed to retrieve equity snapshots: {e}", exc_info=True)
            try:
                self.db.rollback()
            except Exception:
                pass
            # Fallback: return only current state
            return [{
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "value_usd": round(current_value, 2),
                "pnl_usd": round(realized_pnl + unrealized_pnl, 2),
                "sol_balance": round(sol_balance, 4)
            }]

    async def record_equity_snapshot(
        self, 
        wallet_address: str, 
        portfolio_value_usd: float,
        sol_balance: float,
        sol_price_usd: float,
        token_holdings_value_usd: float,
        realized_pnl_usd: float,
        unrealized_pnl_usd: float,
        trigger_type: str = "transaction",
        trigger_reason: str = None
    ) -> bool:
        """
        Records a snapshot of portfolio equity at current timestamp.
        Used to build accurate history of portfolio value changes.
        
        Args:
            wallet_address: The wallet being tracked
            portfolio_value_usd: Total portfolio value in USD
            sol_balance: Native SOL balance
            sol_price_usd: Current SOL price
            token_holdings_value_usd: Value of all SPL token holdings
            realized_pnl_usd: Realized PnL from closed trades
            unrealized_pnl_usd: Unrealized PnL from open positions
            trigger_type: 'transaction' | 'periodic' | 'manual'
            trigger_reason: Description of what triggered this snapshot
        
        Returns:
            True if snapshot was recorded, False on failure

        Note:
            Uses a short-lived, dedicated Session (opened, committed, closed within
            this call) instead of the long-lived shared `self.db` session. The shared
            session is held open for the entire app lifetime and touched concurrently
            by many other background tasks (monitor loop, trigger engine, retrain
            scheduler, etc). Writing through it from here could collide with a
            transaction another task left open on that same session, producing
            'database is locked' errors. A dedicated session keeps this write's
            transaction minimal and isolated, mirroring the pattern already used in
            app.core.error_handler.log_system_error.
        """
        try:
            from app.infrastructure.database.models import EquitySnapshotORM
            from app.infrastructure.database.session import SessionLocal
            from datetime import datetime, timezone
            import uuid

            session = SessionLocal()
            try:
                snapshot = EquitySnapshotORM(
                    snapshot_id=str(uuid.uuid4()),
                    wallet_address=wallet_address,
                    timestamp=datetime.now(timezone.utc),
                    portfolio_value_usd=portfolio_value_usd,
                    sol_balance=sol_balance,
                    sol_price_usd=sol_price_usd,
                    token_holdings_value_usd=token_holdings_value_usd,
                    realized_pnl_usd=realized_pnl_usd,
                    unrealized_pnl_usd=unrealized_pnl_usd,
                    total_pnl_usd=realized_pnl_usd + unrealized_pnl_usd,
                    trigger_type=trigger_type,
                    trigger_reason=trigger_reason,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(snapshot)
                session.commit()
                logger.debug(f"[PNL CALCULATOR] Recorded equity snapshot for {wallet_address[:8]}... value=${portfolio_value_usd:.2f}")
                return True
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            logger.error(f"[PNL CALCULATOR] Failed to record equity snapshot: {e}", exc_info=True)
            return False

    async def get_equity_history(
        self, 
        wallet_address: str, 
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Retrieves equity snapshots from the database for the given timeframe.
        
        Args:
            wallet_address: The wallet to get history for
            days: Number of days of history to retrieve
        
        Returns:
            List of equity history samples with timestamp, value_usd, pnl_usd
        """
        if not self.db:
            logger.debug("[PNL CALCULATOR] DB session not available, cannot retrieve equity snapshots")
            return []
        
        try:
            from app.infrastructure.database.models import EquitySnapshotORM
            from datetime import datetime, timezone, timedelta
            
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
            
            snapshots = self.db.query(EquitySnapshotORM).filter(
                EquitySnapshotORM.wallet_address == wallet_address,
                EquitySnapshotORM.timestamp >= cutoff_time
            ).order_by(EquitySnapshotORM.timestamp.asc()).all()
            
            history = []
            for snap in snapshots:
                history.append({
                    "timestamp": snap.timestamp.isoformat(),
                    "value_usd": snap.portfolio_value_usd,
                    "pnl_usd": snap.total_pnl_usd,
                    "sol_balance": snap.sol_balance
                })
            
            logger.debug(f"[PNL CALCULATOR] Retrieved {len(history)} equity snapshots for {wallet_address[:8]}... in past {days} days")
            return history
        except Exception as e:
            logger.error(f"[PNL CALCULATOR] Failed to retrieve equity snapshots: {e}", exc_info=True)
            return []

    async def ensure_initial_snapshot(
        self,
        wallet_address: str,
        portfolio_value_usd: float = 0.0,
        sol_balance: float = 0.0,
        sol_price_usd: float = 77.34
    ) -> bool:
        """
        Ensures that a wallet has at least one equity snapshot recorded.
        Used when a wallet is first discovered to establish baseline.
        
        If wallet already has snapshots, does nothing.
        If not, creates an initial snapshot with given values.
        
        Args:
            wallet_address: The wallet address
            portfolio_value_usd: Initial portfolio value
            sol_balance: Initial SOL balance
            sol_price_usd: SOL price at snapshot time
        
        Returns:
            True if snapshot was created or already exists, False if error

        Note:
            Uses a short-lived, dedicated Session for the same reason as
            record_equity_snapshot above (avoids contending with the long-lived
            shared `self.db` session used across background tasks).
        """
        try:
            from app.infrastructure.database.models import EquitySnapshotORM
            from app.infrastructure.database.session import SessionLocal
            from datetime import datetime, timezone
            import uuid

            session = SessionLocal()
            try:
                # Check if wallet already has any snapshots
                existing = session.query(EquitySnapshotORM).filter(
                    EquitySnapshotORM.wallet_address == wallet_address
                ).first()

                if existing:
                    logger.debug(f"[PNL CALCULATOR] Wallet {wallet_address[:8]}... already has snapshots")
                    return True

                # Create initial snapshot
                initial_snapshot = EquitySnapshotORM(
                    snapshot_id=str(uuid.uuid4()),
                    wallet_address=wallet_address,
                    timestamp=datetime.now(timezone.utc),
                    portfolio_value_usd=portfolio_value_usd,
                    sol_balance=sol_balance,
                    sol_price_usd=sol_price_usd,
                    token_holdings_value_usd=0.0,
                    realized_pnl_usd=0.0,
                    unrealized_pnl_usd=0.0,
                    total_pnl_usd=0.0,
                    trigger_type="manual",
                    trigger_reason="initial_wallet_setup",
                    created_at=datetime.now(timezone.utc)
                )

                session.add(initial_snapshot)
                session.commit()

                logger.info(f"[PNL CALCULATOR] Created initial equity snapshot for {wallet_address[:8]}... (value=${portfolio_value_usd:.2f})")
                return True
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        except Exception as e:
            logger.error(f"[PNL CALCULATOR] Failed to create initial snapshot: {e}", exc_info=True)
            return False

    async def populate_historical_snapshots_if_empty(self, wallet_address: str):
        """
        Previously populated historical snapshots from closed trades.
        
        DISABLED: Bootstrap trade data generated bad/extreme values that corrupted the equity
        chart. Real equity history now relies exclusively on periodic polling snapshots recorded
        by portfolio_polling_loop in main.py (hourly_baseline + change-triggered snapshots).
        
        If no snapshots exist for the wallet, the first periodic poll will seed the baseline.
        """
        logger.info(
            f"[PNL CALCULATOR] populate_historical_snapshots_if_empty called for "
            f"{wallet_address[:8]}... — skipping, using periodic polling as source of truth."
        )