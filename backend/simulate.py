import asyncio
import logging
import sys
import re
import os
os.environ["SIMULATION_MODE"] = "True"
from datetime import datetime, timezone, timedelta

from app.core.terminal_formatter import setup_terminal_logging, GREEN, RED, YELLOW, CYAN, MAGENTA, BLUE, BOLD, RESET
setup_terminal_logging()

from app.core.config import settings
from app.infrastructure.database.session import SessionLocal, Base, engine
from app.infrastructure.database.repository import (
    SQLAlchemyWalletRepository,
    SQLAlchemyFilterLogRepository,
    SQLAlchemyCooldownRepository,
    SQLAlchemyTradeHistoryRepository,
    SQLAlchemyModelRegistryRepository,
    SQLAlchemyPositionRepository
)
from app.blockchain.monitor import SolanaMonitorSimulator
from app.infrastructure.blockchain.token_service import SolanaTokenInfoService, SolanaTokenSafetyService
from app.use_cases.safety_check_gate import SafetyCheckGate
from app.use_cases.trigger_engine import TriggerEngine
from app.use_cases.relevance_filter import RelevanceFilter
from app.use_cases.monitor_wallets import MonitorWalletsUseCase
from app.domain.interfaces import IMLPipeline

logger = logging.getLogger("simulation")


class ConsoleMLPipeline(IMLPipeline):
    async def analyze_token(self, token_address: str, wallet_source: str, confidence_boost: bool) -> None:
        print(f"\n🚀 {MAGENTA}{BOLD}[ML PIPELINE] [TRIGGERED]{RESET} Token: {CYAN}{token_address}{RESET} from wallet: {YELLOW}{wallet_source}{RESET} (boost: {confidence_boost})\n")


async def main():
    print(f"\n{CYAN}{BOLD}+--------------------------------------------------------+{RESET}")
    print(f"{CYAN}{BOLD}|          AI SMART MONEY TRADING SYSTEM MONITOR         |{RESET}")
    print(f"{CYAN}{BOLD}|                   - SIMULATOR MODE -                   |{RESET}")
    print(f"{CYAN}{BOLD}+--------------------------------------------------------+{RESET}\n")
    
    # Initialize DB tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    wallet_repo = SQLAlchemyWalletRepository(db)
    filter_log_repo = SQLAlchemyFilterLogRepository(db)
    cooldown_repo = SQLAlchemyCooldownRepository(db)
    trade_history_repo = SQLAlchemyTradeHistoryRepository(db)
    model_registry_repo = SQLAlchemyModelRegistryRepository(db)
    position_repo = SQLAlchemyPositionRepository(db)
    
    # Make sure we use base58-valid targets
    settings.TARGET_WALLETS = [
        "Wha1eA11111111111111111111111111111111111",
        "Wha1eB22222222222222222222222222222222222"
    ]
    
    # Pre-register wallets to DB watchlist
    for idx, address in enumerate(settings.TARGET_WALLETS):
        existing = await wallet_repo.get_wallet(address)
        if not existing:
            label = f"Auto Whale {chr(65 + idx)}"
            from app.domain.models import WatchlistWallet
            new_wallet = WatchlistWallet(
                wallet_address=address,
                label=label,
                source="manual",
                added_at=datetime.now(timezone.utc),
                active=True
            )
            await wallet_repo.add_wallet(new_wallet)
            
    active_wallets = await wallet_repo.get_active_wallets()
    wallet_addresses = [w.wallet_address for w in active_wallets]
    
    token_info_service = SolanaTokenInfoService()
    safety_service = SolanaTokenSafetyService()
    safety_check_gate = SafetyCheckGate(safety_service, filter_log_repo)
    
    from app.main import StubMLPipeline
    ml_pipeline = StubMLPipeline(
        trade_history_repo=trade_history_repo,
        token_info_service=token_info_service,
        model_registry_repo=model_registry_repo,
        safety_check_gate=safety_check_gate
    )
    
    # Enable automatic trade execution in safety gate for simulations
    from app.use_cases.auto_trade_executor import AutoTradeExecutor
    auto_trade_executor = AutoTradeExecutor(
        position_repo=position_repo,
        cooldown_repo=cooldown_repo,
        model_registry_repo=model_registry_repo,
        trade_history_repo=trade_history_repo,
        token_info_service=token_info_service,
        token_safety_service=safety_service
    )
    safety_check_gate.auto_trade_executor = auto_trade_executor
    
    trigger_engine = TriggerEngine(
        cooldown_repo=cooldown_repo,
        token_info_service=token_info_service,
        ml_pipeline=ml_pipeline,
        position_repo=position_repo
    )
    
    # F-12 Dynamic Wallet Discovery background service
    from app.use_cases.wallet_discovery import WalletDiscoveryService
    wallet_discovery_service = WalletDiscoveryService(wallet_repo, token_info_service)
    await wallet_discovery_service.start()
    
    # F-13 Token Age & Liquidity Hard Filter
    from app.infrastructure.database.repository import SQLAlchemyHardFilterLogRepository
    from app.use_cases.hard_filter import TokenAgeLiquidityHardFilter
    hard_filter_log_repo = SQLAlchemyHardFilterLogRepository(db)
    hard_filter = TokenAgeLiquidityHardFilter(
        token_info_service=token_info_service,
        trigger_engine=trigger_engine,
        hard_filter_log_repo=hard_filter_log_repo
    )

    relevance_filter = RelevanceFilter(
        filter_log_repo=filter_log_repo,
        trigger_engine=trigger_engine,
        wallet_repo=wallet_repo,
        wallet_discovery_service=wallet_discovery_service,
        hard_filter=hard_filter
    )
    
    # Initialize the simulation monitor instead of real websocket client
    monitor = SolanaMonitorSimulator(wallets=wallet_addresses)
    
    monitor_use_case = MonitorWalletsUseCase(wallet_repo, monitor, relevance_filter)
    await monitor_use_case.initialize_and_start()
    
    print(f"{GREEN}{BOLD}Simulation is running successfully. Sinyal tiruan akan masuk setiap 15-30 detik.{RESET}")
    print(f"{YELLOW}Press Ctrl+C to stop the simulation.{RESET}\n")
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Stopping simulation...{RESET}")
    finally:
        await monitor_use_case.stop()
        try:
            await wallet_discovery_service.stop()
        except Exception:
            pass
        db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass