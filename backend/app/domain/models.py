from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class WatchlistWallet(BaseModel):
    wallet_address: str
    label: str
    source: str  # 'manual' | 'auto_discovered'
    added_at: datetime
    active: bool = True
    status: Optional[str] = "pending"  # 'pending' | 'approved' | 'rejected'


class OpenPosition(BaseModel):
    position_id: str
    wallet_source: str
    token_address: str
    state: str  # 'PENDING_ENTRY' | 'OPEN' | 'EXITING' | 'FAILED_ENTRY' | 'STUCK'
    entry_price: Optional[float] = None
    entry_ts: Optional[datetime] = None
    sl_initial: float
    risk_pct: float
    position_size_usd: float
    trailing_active: bool = False
    trailing_level: Optional[float] = None
    peak_r_multiple: float = 0.0
    confidence_score: float
    model_version: str
    slippage_actual: Optional[float] = None
    unrealized_pnl_usd: Optional[float] = None

    model_config = {
        "protected_namespaces": ()
    }


class ClosedTrade(BaseModel):
    trade_id: str
    wallet_source: str
    token_address: str
    token_symbol: str
    signal_ts: datetime
    entry_ts: datetime
    exit_ts: datetime
    direction: str  # 'BUY' | 'SELL'
    confidence_score: float
    safety_check_passed: bool
    entry_price: float
    exit_price: float
    position_size_usd: float
    risk_pct: float
    pnl_pct_actual: float
    r_multiple: float
    label: str  # 'BUY_BENAR' | 'SALAH' | 'HOLD'
    holding_time_minutes: int
    exit_reason: str  # 'SL' | 'trailing_tp' | 'kill_switch_lp' | 'kill_switch_dev_dump' | 'kill_switch_slippage' | 'manual' | 'liquidity_exhausted'
    is_paper_trade: bool
    is_bootstrap: bool = False
    model_version: str
    slippage_actual: Optional[float] = None

    model_config = {
        "protected_namespaces": ()
    }


class ModelRegistry(BaseModel):
    model_version: str
    trained_at: datetime
    training_sample_count: int
    validation_accuracy: float
    expectancy_r: float
    is_active: bool = False
    rolled_back: bool = False

    model_config = {
        "protected_namespaces": ()
    }


class CooldownState(BaseModel):
    wallet_address: str
    token_address: str
    last_trigger_ts: datetime
    active_position_id: Optional[str] = None


class OnchainEvent(BaseModel):
    event_id: str
    position_id: str
    event_type: str  # 'lp_removal' | 'dev_sell' | 'slippage_spike' | 'holder_shift'
    event_time: datetime
    raw_payload: str  # JSON dump of event
    triggered_exit: bool


class FilterAuditLog(BaseModel):
    log_id: str
    signature: str
    wallet_address: str
    event_type: str
    token_mint: Optional[str] = None
    amount_usd: float
    is_relevant: bool
    reason: str
    timestamp: datetime


class FeatureVector(BaseModel):
    # Trigger metadata
    token_address: str
    wallet_source: str
    signature: str
    timestamp: datetime
    
    # On-chain features
    position_size_usd: float
    token_age_minutes: float
    liquidity_pool_depth: float
    slippage_actual: Optional[float] = None
    cluster_score: float = 0.0
    
    # Historical features
    win_rate_30d: float
    avg_holding_time_minutes: float
    typical_trade_size_usd: float
    past_exit_pattern_score: float = 0.0
    
    # Market context features
    sol_usd_momentum: float = 0.0
    token_volume_liquidity_ratio: float = 0.0
    hour_of_day_utc: int


class PredictionResult(BaseModel):
    direction: str  # 'BUY' | 'SELL' | 'HOLD'
    confidence_score: float
    target_price_estimate: float
    token_address: str
    wallet_source: str
    signature: str
    timestamp: datetime
    # Flag set by TriggerEngine when an expired cooldown is cleared.
    # TradeGuard should skip idempotency check when this is True to prevent deadlock.
    cooldown_already_cleared: bool = False


class SafetyCheckResult(BaseModel):
    token_address: str
    passed: bool
    reason: str
    liquidity_locked: bool
    contract_verified: bool
    top_10_holders_share: float
    mint_authority_revoked: bool
    deployer_holding_pct: Optional[float] = 0.0
    timestamp: datetime


class HardFilterAuditLog(BaseModel):
    log_id: str
    token_address: str
    age_minutes: float
    liquidity_usd: float
    passed: bool
    reason: Optional[str] = None  # 'age' | 'liquidity' | 'dexscreener_failed' | 'token_not_found'
    timestamp: datetime


class RpcFailoverEvent(BaseModel):
    event_type: str  # 'failover' | 'degraded' | 'recovery'
    source: str      # 'primary' | 'secondary'
    target_url: str
    timestamp: datetime

    model_config = {
        "protected_namespaces": ()
    }


class SystemErrorLog(BaseModel):
    log_id: str
    timestamp: datetime
    error_type: str
    severity: str
    context: str
    recovery_action: str
    resolution_status: str

    model_config = {
        "protected_namespaces": ()
    }


