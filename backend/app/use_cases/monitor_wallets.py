import asyncio
import logging
from datetime import datetime, timezone
from app.core.config import settings
from app.domain.interfaces import IWalletRepository, IWalletMovementMonitor, IRelevanceFilter
from app.domain.models import WatchlistWallet

logger = logging.getLogger(__name__)


class MonitorWalletsUseCase:
    """
    Use Case: Monitor Wallets (F-01 Orchestrator)
    - Automatically syncs config target wallets to SQLite database.
    - Loads active wallets from DB.
    - Starts the real-time Solana RPC monitor.
    - Consumes the queue of parsed transaction events and feeds them to Relevance Filter.
    """
    def __init__(
        self,
        wallet_repo: IWalletRepository,
        monitor: IWalletMovementMonitor,
        relevance_filter: IRelevanceFilter
    ):
        self.wallet_repo = wallet_repo
        self.monitor = monitor
        self.relevance_filter = relevance_filter
        self.is_running = False
        self.consumer_task: Optional[asyncio.Task] = None

    async def initialize_and_start(self) -> None:
        """
        Runs pendaftaran otomatis, loads watchlist, starts WebSocket monitor,
        and launches the event consumer worker.
        """
        # 1. Auto register wallets from config.yaml target list
        await self._auto_register_config_wallets()

        # 2. Fetch active target wallets from SQLite database
        active_wallets = await self.wallet_repo.get_active_wallets()
        wallet_addresses = [w.wallet_address for w in active_wallets]

        # 3. Update monitor list and start subscription
        # If in simulator fallback (e.g. testing / offline), update dynamic list
        if hasattr(self.monitor, "update_wallets"):
            self.monitor.update_wallets(wallet_addresses)
        else:
            # For simulator fallback, set its wallets list
            self.monitor.wallets = wallet_addresses

        await self.monitor.start()

        # 4. Start consumer loop
        self.is_running = True
        self.consumer_task = asyncio.create_task(self._consume_queue_loop())
        logger.info("MonitorWalletsUseCase successfully initialized and started.")

    async def _auto_register_config_wallets(self) -> None:
        """Checks and inserts configured TARGET_WALLETS if missing from SQLite DB."""
        config_wallets = settings.TARGET_WALLETS
        for idx, address in enumerate(config_wallets):
            # Check database
            existing = await self.wallet_repo.get_wallet(address)
            if not existing:
                # Automatis menandai & memasukkan ke daftar wallet (Auto Whale A, Auto Whale B...)
                label = f"Auto Whale {chr(65 + idx)}"
                new_wallet = WatchlistWallet(
                    wallet_address=address,
                    label=label,
                    source="manual",
                    added_at=datetime.now(timezone.utc),
                    active=True
                )
                await self.wallet_repo.add_wallet(new_wallet)
                logger.info(f"Auto-registered config wallet: {address} with label '{label}' to database.")

    async def _consume_queue_loop(self) -> None:
        """Retrieves parsed events from the queue and routes to Relevance Filter."""
        queue = self.monitor.get_event_queue()
        while self.is_running:
            try:
                event_data = await queue.get()
                await self.relevance_filter.process_event(event_data)
                queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error consuming event in monitor loop: {e}", exc_info=True)

    async def reload_watchlist(self) -> None:
        """Reloads active target wallets from DB and updates monitor subscription list."""
        active_wallets = await self.wallet_repo.get_active_wallets()
        wallet_addresses = [w.wallet_address for w in active_wallets]
        
        if hasattr(self.monitor, "update_wallets"):
            self.monitor.update_wallets(wallet_addresses)
        else:
            self.monitor.wallets = wallet_addresses
        logger.info(f"[MONITOR ORCHESTRATOR] Watchlist reloaded. Monitoring {len(wallet_addresses)} active wallets.")

    async def stop(self) -> None:
        self.is_running = False
        if self.consumer_task:
            self.consumer_task.cancel()
        await self.monitor.stop()
        logger.info("MonitorWalletsUseCase stopped.")
