import asyncio
import functools
import logging
import random
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.infrastructure.database.session import db_lock
from app.domain.models import (
    WatchlistWallet,
    OpenPosition,
    ClosedTrade,
    ModelRegistry,
    CooldownState,
    OnchainEvent,
    FilterAuditLog,
    HardFilterAuditLog,
    SystemErrorLog
)
from app.domain.interfaces import (
    IWalletRepository,
    IPositionRepository,
    ITradeHistoryRepository,
    IModelRegistryRepository,
    ICooldownRepository,
    IOnchainEventRepository,
    IFilterLogRepository,
    IHardFilterLogRepository,
    IErrorLogRepository
)
from app.infrastructure.database.models import (
    WatchlistWalletORM,
    OpenPositionORM,
    ClosedTradeORM,
    ModelRegistryORM,
    CooldownStateORM,
    OnchainEventORM,
    FilterAuditLogORM,
    HardFilterAuditLogORM,
    SystemErrorLogORM
)


logger = logging.getLogger(__name__)


def db_locked(func):
    """
    Every repository below shares ONE SQLAlchemy Session with every other
    background task in the app (see infrastructure/database/session.py for
    why). This decorator serializes all access to that Session behind a
    single asyncio.Lock so two tasks can never touch it at the same time --
    that interleaving, not just raw SQLite contention, is what was producing
    both "database is locked" and the PendingRollbackError cascade that
    followed it.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        async with db_lock:
            return await func(*args, **kwargs)
    return wrapper


def retry_on_db_locked(max_retries: int = 5, base_delay: float = 0.15):
    """
    Applied on top of @db_locked on write methods only. Retries the WHOLE
    method (query + mutate + commit), never just the commit() call itself --
    retrying only commit() was tried before and risked silently losing a
    write if part of the operation had already taken effect. Retrying the
    full method is safe here because a failed commit means nothing was
    durably written, so re-running it from scratch is not a duplicate.
    This only ever fires for genuinely external contention (e.g. a script
    run manually against the same .db file), since db_locked already
    removes contention between the app's own tasks.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return await func(self, *args, **kwargs)
                except OperationalError as e:
                    if "database is locked" not in str(e).lower():
                        raise
                    last_exc = e
                    try:
                        self.db.rollback()
                    except Exception:
                        pass
                    delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                    logger.warning(
                        f"[DB LOCKED] {func.__qualname__} attempt {attempt + 1}/{max_retries} "
                        f"hit 'database is locked', retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
            logger.error(f"[DB LOCKED] {func.__qualname__} exhausted {max_retries} retries.")
            raise last_exc
        return wrapper
    return decorator


class SQLAlchemyWalletRepository(IWalletRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: WatchlistWalletORM) -> WatchlistWallet:
        return WatchlistWallet(
            wallet_address=orm.wallet_address,
            label=orm.label,
            source=orm.source,
            added_at=orm.added_at,
            active=orm.active,
            status=getattr(orm, "status", "pending")
        )

    @db_locked
    async def get_all_wallets(self) -> List[WatchlistWallet]:
        orms = self.db.query(WatchlistWalletORM).all()
        return [self._to_domain(o) for o in orms]

    @db_locked
    async def get_active_wallets(self) -> List[WatchlistWallet]:
        orms = self.db.query(WatchlistWalletORM).filter(WatchlistWalletORM.active == True).all()
        return [self._to_domain(o) for o in orms]

    @db_locked
    async def get_wallet(self, wallet_address: str) -> Optional[WatchlistWallet]:
        orm = self.db.query(WatchlistWalletORM).filter(WatchlistWalletORM.wallet_address == wallet_address).first()
        return self._to_domain(orm) if orm else None

    @retry_on_db_locked()
    @db_locked
    async def add_wallet(self, wallet: WatchlistWallet) -> None:
        orm = WatchlistWalletORM(
            wallet_address=wallet.wallet_address,
            label=wallet.label,
            source=wallet.source,
            added_at=wallet.added_at,
            active=wallet.active,
            status=wallet.status or "pending"
        )
        self.db.add(orm)
        self.db.commit()

    @retry_on_db_locked()
    @db_locked
    async def update_wallet(self, wallet: WatchlistWallet) -> None:
        orm = self.db.query(WatchlistWalletORM).filter(WatchlistWalletORM.wallet_address == wallet.wallet_address).first()
        if orm:
            orm.label = wallet.label
            orm.source = wallet.source
            orm.active = wallet.active
            if hasattr(orm, "status"):
                orm.status = wallet.status or "pending"
            self.db.commit()


class SQLAlchemyPositionRepository(IPositionRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: OpenPositionORM) -> OpenPosition:
        return OpenPosition(
            position_id=orm.position_id,
            wallet_source=orm.wallet_source,
            token_address=orm.token_address,
            state=orm.state,
            entry_price=orm.entry_price,
            entry_ts=orm.entry_ts,
            sl_initial=orm.sl_initial,
            risk_pct=orm.risk_pct,
            position_size_usd=orm.position_size_usd,
            trailing_active=orm.trailing_active,
            trailing_level=orm.trailing_level,
            peak_r_multiple=orm.peak_r_multiple,
            confidence_score=orm.confidence_score,
            model_version=orm.model_version,
            slippage_actual=getattr(orm, "slippage_actual", None)
        )

    @db_locked
    async def get_open_positions(self) -> List[OpenPosition]:
        orms = self.db.query(OpenPositionORM).filter(OpenPositionORM.state.in_(["OPEN", "PENDING_ENTRY", "EXITING"])).all()
        return [self._to_domain(o) for o in orms]

    @db_locked
    async def get_position(self, position_id: str) -> Optional[OpenPosition]:
        orm = self.db.query(OpenPositionORM).filter(OpenPositionORM.position_id == position_id).first()
        return self._to_domain(orm) if orm else None

    @retry_on_db_locked()
    @db_locked
    async def add_position(self, position: OpenPosition) -> None:
        orm = OpenPositionORM(
            position_id=position.position_id,
            wallet_source=position.wallet_source,
            token_address=position.token_address,
            state=position.state,
            entry_price=position.entry_price,
            entry_ts=position.entry_ts,
            sl_initial=position.sl_initial,
            risk_pct=position.risk_pct,
            position_size_usd=position.position_size_usd,
            trailing_active=position.trailing_active,
            trailing_level=position.trailing_level,
            peak_r_multiple=position.peak_r_multiple,
            confidence_score=position.confidence_score,
            model_version=position.model_version,
            slippage_actual=position.slippage_actual
        )
        self.db.add(orm)
        self.db.commit()

    @retry_on_db_locked()
    @db_locked
    async def update_position(self, position: OpenPosition) -> None:
        orm = self.db.query(OpenPositionORM).filter(OpenPositionORM.position_id == position.position_id).first()
        if orm:
            orm.state = position.state
            orm.entry_price = position.entry_price
            orm.entry_ts = position.entry_ts
            orm.sl_initial = position.sl_initial
            orm.risk_pct = position.risk_pct
            orm.position_size_usd = position.position_size_usd
            orm.trailing_active = position.trailing_active
            orm.trailing_level = position.trailing_level
            orm.peak_r_multiple = position.peak_r_multiple
            orm.confidence_score = position.confidence_score
            orm.model_version = position.model_version
            if hasattr(orm, "slippage_actual"):
                orm.slippage_actual = position.slippage_actual
            self.db.commit()

    @retry_on_db_locked()
    @db_locked
    async def delete_position(self, position_id: str) -> None:
        orm = self.db.query(OpenPositionORM).filter(OpenPositionORM.position_id == position_id).first()
        if orm:
            self.db.delete(orm)
            self.db.commit()


class SQLAlchemyTradeHistoryRepository(ITradeHistoryRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: ClosedTradeORM) -> ClosedTrade:
        return ClosedTrade(
            trade_id=orm.trade_id,
            wallet_source=orm.wallet_source,
            token_address=orm.token_address,
            token_symbol=orm.token_symbol,
            signal_ts=orm.signal_ts,
            entry_ts=orm.entry_ts,
            exit_ts=orm.exit_ts,
            direction=orm.direction,
            confidence_score=orm.confidence_score,
            safety_check_passed=orm.safety_check_passed,
            entry_price=orm.entry_price,
            exit_price=orm.exit_price,
            position_size_usd=orm.position_size_usd,
            risk_pct=orm.risk_pct,
            pnl_pct_actual=orm.pnl_pct_actual,
            r_multiple=orm.r_multiple,
            label=orm.label,
            holding_time_minutes=orm.holding_time_minutes,
            exit_reason=orm.exit_reason,
            is_paper_trade=orm.is_paper_trade,
            is_bootstrap=orm.is_bootstrap or False,
            model_version=orm.model_version,
            slippage_actual=getattr(orm, "slippage_actual", None)
        )

    @db_locked
    async def get_closed_trades(self, limit: int = 100, offset: int = 0, exclude_bootstrap: bool = False) -> List[ClosedTrade]:
        query = self.db.query(ClosedTradeORM)
        if exclude_bootstrap:
            query = query.filter((ClosedTradeORM.is_bootstrap == False) | (ClosedTradeORM.is_bootstrap == None))
        orms = query.order_by(ClosedTradeORM.exit_ts.desc()).limit(limit).offset(offset).all()
        return [self._to_domain(o) for o in orms]

    @db_locked
    async def get_closed_trades_count(self, exclude_bootstrap: bool = False) -> int:
        query = self.db.query(ClosedTradeORM)
        if exclude_bootstrap:
            query = query.filter((ClosedTradeORM.is_bootstrap == False) | (ClosedTradeORM.is_bootstrap == None))
        return query.count()

    @retry_on_db_locked()
    @db_locked
    async def add_closed_trade(self, trade: ClosedTrade) -> None:
        orm = ClosedTradeORM(
            trade_id=trade.trade_id,
            wallet_source=trade.wallet_source,
            token_address=trade.token_address,
            token_symbol=trade.token_symbol,
            signal_ts=trade.signal_ts,
            entry_ts=trade.entry_ts,
            exit_ts=trade.exit_ts,
            direction=trade.direction,
            confidence_score=trade.confidence_score,
            safety_check_passed=trade.safety_check_passed,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            position_size_usd=trade.position_size_usd,
            risk_pct=trade.risk_pct,
            pnl_pct_actual=trade.pnl_pct_actual,
            r_multiple=trade.r_multiple,
            label=trade.label,
            holding_time_minutes=trade.holding_time_minutes,
            exit_reason=trade.exit_reason,
            is_paper_trade=trade.is_paper_trade,
            is_bootstrap=trade.is_bootstrap or False,
            model_version=trade.model_version,
            slippage_actual=trade.slippage_actual
        )
        self.db.add(orm)
        self.db.commit()


class SQLAlchemyModelRegistryRepository(IModelRegistryRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: ModelRegistryORM) -> ModelRegistry:
        return ModelRegistry(
            model_version=orm.model_version,
            trained_at=orm.trained_at,
            training_sample_count=orm.training_sample_count,
            validation_accuracy=orm.validation_accuracy,
            expectancy_r=orm.expectancy_r,
            is_active=orm.is_active,
            rolled_back=orm.rolled_back
        )

    @db_locked
    async def get_active_model(self) -> Optional[ModelRegistry]:
        orm = self.db.query(ModelRegistryORM).filter(ModelRegistryORM.is_active == True).first()
        return self._to_domain(orm) if orm else None

    @retry_on_db_locked()
    @db_locked
    async def add_model_version(self, model: ModelRegistry) -> None:
        orm = ModelRegistryORM(
            model_version=model.model_version,
            trained_at=model.trained_at,
            training_sample_count=model.training_sample_count,
            validation_accuracy=model.validation_accuracy,
            expectancy_r=model.expectancy_r,
            is_active=model.is_active,
            rolled_back=model.rolled_back
        )
        self.db.add(orm)
        self.db.commit()

    @retry_on_db_locked()
    @db_locked
    async def update_model_version(self, model: ModelRegistry) -> None:
        orm = self.db.query(ModelRegistryORM).filter(ModelRegistryORM.model_version == model.model_version).first()
        if orm:
            orm.is_active = model.is_active
            orm.rolled_back = model.rolled_back
            orm.validation_accuracy = model.validation_accuracy
            orm.expectancy_r = model.expectancy_r
            self.db.commit()

    @db_locked
    async def get_model_version(self, model_version: str) -> Optional[ModelRegistry]:
        orm = self.db.query(ModelRegistryORM).filter(ModelRegistryORM.model_version == model_version).first()
        return self._to_domain(orm) if orm else None


class SQLAlchemyCooldownRepository(ICooldownRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: CooldownStateORM) -> CooldownState:
        return CooldownState(
            wallet_address=orm.wallet_address,
            token_address=orm.token_address,
            last_trigger_ts=orm.last_trigger_ts,
            active_position_id=orm.active_position_id
        )

    @db_locked
    async def get_cooldown(self, wallet_address: str, token_address: str) -> Optional[CooldownState]:
        orm = self.db.query(CooldownStateORM).filter(
            CooldownStateORM.wallet_address == wallet_address,
            CooldownStateORM.token_address == token_address
        ).first()
        return self._to_domain(orm) if orm else None

    @retry_on_db_locked()
    @db_locked
    async def set_cooldown(self, cooldown: CooldownState) -> None:
        orm = self.db.query(CooldownStateORM).filter(
            CooldownStateORM.wallet_address == cooldown.wallet_address,
            CooldownStateORM.token_address == cooldown.token_address
        ).first()
        if orm:
            orm.last_trigger_ts = cooldown.last_trigger_ts
            orm.active_position_id = cooldown.active_position_id
        else:
            orm = CooldownStateORM(
                wallet_address=cooldown.wallet_address,
                token_address=cooldown.token_address,
                last_trigger_ts=cooldown.last_trigger_ts,
                active_position_id=cooldown.active_position_id
            )
            self.db.add(orm)
        self.db.commit()

    @retry_on_db_locked()
    @db_locked
    async def delete_cooldown(self, wallet_address: str, token_address: str) -> None:
        orm = self.db.query(CooldownStateORM).filter(
            CooldownStateORM.wallet_address == wallet_address,
            CooldownStateORM.token_address == token_address
        ).first()
        if orm:
            self.db.delete(orm)
            self.db.commit()


class SQLAlchemyOnchainEventRepository(IOnchainEventRepository):
    def __init__(self, db: Session):
        self.db = db

    @retry_on_db_locked()
    @db_locked
    async def add_event(self, event: OnchainEvent) -> None:
        orm = OnchainEventORM(
            event_id=event.event_id,
            position_id=event.position_id,
            event_type=event.event_type,
            event_time=event.event_time,
            raw_payload=event.raw_payload,
            triggered_exit=event.triggered_exit
        )
        self.db.add(orm)
        self.db.commit()


class SQLAlchemyFilterLogRepository(IFilterLogRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: FilterAuditLogORM) -> FilterAuditLog:
        return FilterAuditLog(
            log_id=orm.log_id,
            signature=orm.signature,
            wallet_address=orm.wallet_address,
            event_type=orm.event_type,
            token_mint=orm.token_mint,
            amount_usd=orm.amount_usd,
            is_relevant=orm.is_relevant,
            reason=orm.reason,
            timestamp=orm.timestamp
        )

    @retry_on_db_locked()
    @db_locked
    async def add_log(self, log: FilterAuditLog) -> None:
        orm = FilterAuditLogORM(
            log_id=log.log_id,
            signature=log.signature,
            wallet_address=log.wallet_address,
            event_type=log.event_type,
            token_mint=log.token_mint,
            amount_usd=log.amount_usd,
            is_relevant=log.is_relevant,
            reason=log.reason,
            timestamp=log.timestamp
        )
        self.db.add(orm)
        self.db.commit()

    @db_locked
    async def get_logs(self, limit: int = 100) -> List[FilterAuditLog]:
        orms = self.db.query(FilterAuditLogORM).order_by(FilterAuditLogORM.timestamp.desc()).limit(limit).all()
        return [self._to_domain(o) for o in orms]


class SQLAlchemyHardFilterLogRepository(IHardFilterLogRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: HardFilterAuditLogORM) -> HardFilterAuditLog:
        return HardFilterAuditLog(
            log_id=orm.log_id,
            token_address=orm.token_address,
            age_minutes=orm.age_minutes,
            liquidity_usd=orm.liquidity_usd,
            passed=orm.passed,
            reason=orm.reason,
            timestamp=orm.timestamp
        )

    @retry_on_db_locked()
    @db_locked
    async def add_hard_filter_log(self, log: HardFilterAuditLog) -> None:
        orm = HardFilterAuditLogORM(
            log_id=log.log_id,
            token_address=log.token_address,
            age_minutes=log.age_minutes,
            liquidity_usd=log.liquidity_usd,
            passed=log.passed,
            reason=log.reason,
            timestamp=log.timestamp
        )
        self.db.add(orm)
        self.db.commit()

    @db_locked
    async def get_hard_filter_logs(self, limit: int = 100) -> List[HardFilterAuditLog]:
        orms = self.db.query(HardFilterAuditLogORM).order_by(HardFilterAuditLogORM.timestamp.desc()).limit(limit).all()
        return [self._to_domain(o) for o in orms]


class SQLAlchemyErrorLogRepository(IErrorLogRepository):
    def __init__(self, db: Session):
        self.db = db

    def _to_domain(self, orm: SystemErrorLogORM) -> SystemErrorLog:
        return SystemErrorLog(
            log_id=orm.log_id,
            timestamp=orm.timestamp,
            error_type=orm.error_type,
            severity=orm.severity,
            context=orm.context,
            recovery_action=orm.recovery_action,
            resolution_status=orm.resolution_status
        )

    @retry_on_db_locked()
    @db_locked
    async def log_error(self, error_log: SystemErrorLog) -> None:
        orm = SystemErrorLogORM(
            log_id=error_log.log_id,
            timestamp=error_log.timestamp,
            error_type=error_log.error_type,
            severity=error_log.severity,
            context=error_log.context,
            recovery_action=error_log.recovery_action,
            resolution_status=error_log.resolution_status
        )
        self.db.add(orm)
        self.db.commit()

    @db_locked
    async def get_error_logs(self, limit: int = 100) -> List[SystemErrorLog]:
        orms = self.db.query(SystemErrorLogORM).order_by(SystemErrorLogORM.timestamp.desc()).limit(limit).all()
        return [self._to_domain(o) for o in orms]