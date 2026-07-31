from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.orm import relationship
from app.infrastructure.database.session import Base


class WatchlistWalletORM(Base):
    __tablename__ = "watchlist_wallets"

    wallet_address = Column(String, primary_key=True, index=True)
    label = Column(String, nullable=False)
    source = Column(String, nullable=False)  # 'manual' | 'auto_discovered'
    added_at = Column(DateTime, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    status = Column(String, default="pending", nullable=True)  # 'pending' | 'approved' | 'rejected'


class ModelRegistryORM(Base):
    __tablename__ = "model_registry"

    model_version = Column(String, primary_key=True)
    trained_at = Column(DateTime, nullable=False)
    training_sample_count = Column(Integer, nullable=False)
    validation_accuracy = Column(Float, nullable=False)
    expectancy_r = Column(Float, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    rolled_back = Column(Boolean, default=False, nullable=False)


class OpenPositionORM(Base):
    __tablename__ = "open_positions"

    position_id = Column(String, primary_key=True)
    wallet_source = Column(String, ForeignKey("watchlist_wallets.wallet_address"), nullable=False)
    token_address = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False)  # 'PENDING_ENTRY' | 'OPEN' | 'EXITING' | 'FAILED_ENTRY' | 'STUCK'
    entry_price = Column(Float, nullable=True)
    entry_ts = Column(DateTime, nullable=True)
    sl_initial = Column(Float, nullable=False)
    risk_pct = Column(Float, nullable=False)
    position_size_usd = Column(Float, nullable=False)
    trailing_active = Column(Boolean, default=False, nullable=False)
    trailing_level = Column(Float, nullable=True)
    peak_r_multiple = Column(Float, default=0.0, nullable=False)
    confidence_score = Column(Float, nullable=False)
    model_version = Column(String, ForeignKey("model_registry.model_version"), nullable=False)
    slippage_actual = Column(Float, nullable=True)

    wallet = relationship("WatchlistWalletORM")
    model = relationship("ModelRegistryORM")


class ClosedTradeORM(Base):
    __tablename__ = "closed_trades"

    trade_id = Column(String, primary_key=True)
    wallet_source = Column(String, ForeignKey("watchlist_wallets.wallet_address"), nullable=False)
    token_address = Column(String, nullable=False, index=True)
    token_symbol = Column(String, nullable=False)
    signal_ts = Column(DateTime, nullable=False)
    entry_ts = Column(DateTime, nullable=False)
    exit_ts = Column(DateTime, nullable=False)
    direction = Column(String, nullable=False)  # 'BUY' | 'SELL'
    confidence_score = Column(Float, nullable=False)
    safety_check_passed = Column(Boolean, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    position_size_usd = Column(Float, nullable=False)
    risk_pct = Column(Float, nullable=False)
    pnl_pct_actual = Column(Float, nullable=False)
    r_multiple = Column(Float, nullable=False)
    label = Column(String, nullable=False)  # 'BUY_BENAR' | 'SALAH' | 'HOLD'
    holding_time_minutes = Column(Integer, nullable=False)
    exit_reason = Column(String, nullable=False)  # e.g., 'SL', 'trailing_tp', 'kill_switch_lp'
    is_paper_trade = Column(Boolean, nullable=False)
    is_bootstrap = Column(Boolean, default=False, nullable=True)
    model_version = Column(String, ForeignKey("model_registry.model_version"), nullable=False)
    slippage_actual = Column(Float, nullable=True)

    wallet = relationship("WatchlistWalletORM")
    model = relationship("ModelRegistryORM")


class CooldownStateORM(Base):
    __tablename__ = "cooldown_state"

    wallet_address = Column(String, ForeignKey("watchlist_wallets.wallet_address"), primary_key=True)
    token_address = Column(String, primary_key=True)
    last_trigger_ts = Column(DateTime, nullable=False)
    active_position_id = Column(String, ForeignKey("open_positions.position_id"), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("wallet_address", "token_address"),
    )

    wallet = relationship("WatchlistWalletORM")
    active_position = relationship("OpenPositionORM")


class OnchainEventORM(Base):
    __tablename__ = "onchain_events"

    event_id = Column(String, primary_key=True)
    position_id = Column(String, ForeignKey("open_positions.position_id"), nullable=False)
    event_type = Column(String, nullable=False)  # 'lp_removal' | 'dev_sell' | 'slippage_spike' | 'holder_shift'
    event_time = Column(DateTime, nullable=False)
    raw_payload = Column(String, nullable=False)  # JSON String
    triggered_exit = Column(Boolean, nullable=False)

    position = relationship("OpenPositionORM")


class FilterAuditLogORM(Base):
    __tablename__ = "filter_audit_logs"

    log_id = Column(String, primary_key=True)
    signature = Column(String, nullable=False, index=True)
    wallet_address = Column(String, ForeignKey("watchlist_wallets.wallet_address"), nullable=False)
    event_type = Column(String, nullable=False)
    token_mint = Column(String, nullable=True)
    amount_usd = Column(Float, nullable=False)
    is_relevant = Column(Boolean, nullable=False)
    reason = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)

    wallet = relationship("WatchlistWalletORM")


class HardFilterAuditLogORM(Base):
    __tablename__ = "hard_filter_audit_logs"

    log_id = Column(String, primary_key=True)
    token_address = Column(String, nullable=False, index=True)
    age_minutes = Column(Float, nullable=False)
    liquidity_usd = Column(Float, nullable=False)
    passed = Column(Boolean, nullable=False)
    reason = Column(String, nullable=True)
    timestamp = Column(DateTime, nullable=False)


class SystemErrorLogORM(Base):
    __tablename__ = "system_error_logs"

    log_id = Column(String, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
    error_type = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    context = Column(String, nullable=False)
    recovery_action = Column(String, nullable=False)
    resolution_status = Column(String, nullable=False)


class EquitySnapshotORM(Base):
    __tablename__ = "equity_snapshots"

    snapshot_id = Column(String, primary_key=True)
    wallet_address = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    portfolio_value_usd = Column(Float, nullable=False)
    sol_balance = Column(Float, nullable=False)
    sol_price_usd = Column(Float, nullable=False)
    token_holdings_value_usd = Column(Float, nullable=False)
    realized_pnl_usd = Column(Float, nullable=False)
    unrealized_pnl_usd = Column(Float, nullable=False)
    total_pnl_usd = Column(Float, nullable=False)
    trigger_type = Column(String, nullable=False)
    trigger_reason = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)


