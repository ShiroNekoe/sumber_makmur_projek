"""
F-07 API Schemas
Pydantic models for all Dashboard REST API request/response types.
Kept separate from domain models to maintain clean architecture layer boundaries.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


# ─── Signal Schemas ─────────────────────────────────────────────────────────

class FeatureVectorResponse(BaseModel):
    position_size_usd: float
    token_age_minutes: float
    liquidity_pool_depth: float
    slippage_actual: Optional[float] = None
    cluster_score: float
    win_rate_30d: float
    avg_holding_time_minutes: float
    typical_trade_size_usd: float
    past_exit_pattern_score: float
    sol_usd_momentum: float
    token_volume_liquidity_ratio: float
    hour_of_day_utc: int

    class Config:
        from_attributes = True


class SignalResponse(BaseModel):
    """A single ML prediction signal that passed the safety gate."""
    signal_id: str
    token_address: str
    token_short: str          # Partial address: first 6 + ... + last 4
    wallet_source: str
    wallet_short: str         # Partial address: first 6 + ... + last 4
    direction: str            # 'BUY' | 'SELL' | 'HOLD'
    confidence_score: float
    safety_passed: bool
    timestamp: datetime
    features: Optional[FeatureVectorResponse] = None
    token_symbol: Optional[str] = ""
    token_name: Optional[str] = ""
    dex_url: Optional[str] = ""

    class Config:
        from_attributes = True



class SignalListResponse(BaseModel):
    signals: List[SignalResponse]
    total: int
    hours_window: int


# ─── Trade Schemas ───────────────────────────────────────────────────────────

class TradeResponse(BaseModel):
    """A single closed trade entry."""
    trade_id: str
    token_address: str
    token_symbol: str
    token_short: str          # Partial address
    direction: str            # 'BUY' | 'SELL'
    confidence_score: float
    entry_price: float
    exit_price: float
    pnl_pct_actual: float
    r_multiple: float
    label: str                # 'BUY_BENAR' | 'SALAH' | 'HOLD'
    holding_time_minutes: int
    exit_reason: str
    exit_ts: datetime

    class Config:
        from_attributes = True


class TradeListResponse(BaseModel):
    trades: List[TradeResponse]
    total: int


# ─── Position Schemas ────────────────────────────────────────────────────────

class PositionResponse(BaseModel):
    """A single open position."""
    position_id: str
    token_address: str
    token_short: str          # Partial address
    wallet_source: str
    wallet_short: str         # Partial address
    state: str
    position_size_usd: float
    confidence_score: float
    model_version: str
    entry_ts: Optional[datetime] = None

    class Config:
        from_attributes = True


class PositionListResponse(BaseModel):
    positions: List[PositionResponse]
    total: int


# ─── Stats Schemas ───────────────────────────────────────────────────────────

class DashboardStatsResponse(BaseModel):
    """Aggregated dashboard statistics."""
    win_rate_pct: Optional[float]        # e.g. 68.5 (percentage)
    total_closed_trades: int
    buy_benar_count: int
    triggers_today: int
    alerts_fired_24h: int
    total_signals_24h: int
    open_positions_count: int
    confidence_threshold_pct: float      # From config.yaml (e.g. 75.0)
    active_model_version: str


# ─── Wallet Candidate Schemas ────────────────────────────────────────────────

class WalletCandidateResponse(BaseModel):
    """A wallet auto-discovered by F-12 awaiting approval."""
    wallet_address: str
    wallet_short: str         # Partial address
    label: str
    source: str               # 'auto_discovered'
    discovery_reason: str
    discovered_at: datetime
    status: str               # 'pending' | 'approved' | 'rejected'


class WalletCandidateListResponse(BaseModel):
    candidates: List[WalletCandidateResponse]
    total: int


class WalletApprovalRequest(BaseModel):
    """Body for approving/rejecting a wallet candidate."""
    action: str               # 'approve' | 'reject'
    label: Optional[str] = None   # Custom label for approved wallet


class WalletApprovalResponse(BaseModel):
    wallet_address: str
    action: str
    approval_timestamp: datetime
    success: bool
    message: str


# ─── System Status Schema ────────────────────────────────────────────────────

class ComponentStatus(BaseModel):
    name: str
    status: str               # 'running' | 'error' | 'idle'
    detail: Optional[str] = None


class SystemStatusResponse(BaseModel):
    overall_status: str       # 'healthy' | 'degraded' | 'error'
    rpc_status: str           # 'online' | 'offline' | 'simulation'
    components: List[ComponentStatus]
    timestamp: datetime


# ─── System Error Schemas ─────────────────────────────────────────────────────

class SystemErrorResponse(BaseModel):
    log_id: str
    timestamp: datetime
    error_type: str
    severity: str
    context: str
    recovery_action: str
    resolution_status: str


class SystemErrorListResponse(BaseModel):
    errors: List[SystemErrorResponse]
    total: int


# ─── Portfolio & PnL Schemas ──────────────────────────────────────────────────

class HoldingResponse(BaseModel):
    mint: str
    symbol: str
    name: str
    amount: float
    price_usd: float
    cost_basis: float
    value_usd: float
    unrealized_pnl_usd: float
    unrealized_pnl_pct: float


class HistorySample(BaseModel):
    timestamp: str
    value_usd: float
    pnl_usd: float
    sol_balance: float = 0.0  # SOL balance at this timestamp


class PortfolioSummaryResponse(BaseModel):
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    total_pnl_usd: float
    portfolio_value_usd: float
    holdings: List[HoldingResponse]
    history_1d: List[HistorySample] = []
    history_7d: List[HistorySample] = []
    history_30d: List[HistorySample] = []
    history_180d: List[HistorySample] = []
    history_360d: List[HistorySample] = []


# ─── Wallet Manual Manage Schemas ──────────────────────────────────────────────

class WalletAddRequest(BaseModel):
    wallet_address: str
    label: Optional[str] = ""

class WalletAddResponse(BaseModel):
    success: bool
    message: str

class WalletDeleteResponse(BaseModel):
    success: bool
    message: str

