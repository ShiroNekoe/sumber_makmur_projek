from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime
import asyncio
from app.domain.models import (
    WatchlistWallet,
    OpenPosition,
    ClosedTrade,
    ModelRegistry,
    CooldownState,
    OnchainEvent,
    FilterAuditLog,
    FeatureVector,
    PredictionResult,
    SafetyCheckResult,
    HardFilterAuditLog,
    SystemErrorLog
)


class IWalletRepository(ABC):
    @abstractmethod
    async def get_all_wallets(self) -> List[WatchlistWallet]:
        pass

    @abstractmethod
    async def get_active_wallets(self) -> List[WatchlistWallet]:
        pass

    @abstractmethod
    async def get_wallet(self, wallet_address: str) -> Optional[WatchlistWallet]:
        pass

    @abstractmethod
    async def add_wallet(self, wallet: WatchlistWallet) -> None:
        pass

    @abstractmethod
    async def update_wallet(self, wallet: WatchlistWallet) -> None:
        pass


class IPositionRepository(ABC):
    @abstractmethod
    async def get_open_positions(self) -> List[OpenPosition]:
        pass

    @abstractmethod
    async def get_position(self, position_id: str) -> Optional[OpenPosition]:
        pass

    @abstractmethod
    async def add_position(self, position: OpenPosition) -> None:
        pass

    @abstractmethod
    async def update_position(self, position: OpenPosition) -> None:
        pass

    @abstractmethod
    async def delete_position(self, position_id: str) -> None:
        pass


class ITradeHistoryRepository(ABC):
    @abstractmethod
    async def get_closed_trades(self, limit: int = 100, offset: int = 0) -> List[ClosedTrade]:
        pass

    @abstractmethod
    async def get_closed_trades_count(self) -> int:
        pass

    @abstractmethod
    async def add_closed_trade(self, trade: ClosedTrade) -> None:
        pass


class IModelRegistryRepository(ABC):
    @abstractmethod
    async def get_active_model(self) -> Optional[ModelRegistry]:
        pass

    @abstractmethod
    async def add_model_version(self, model: ModelRegistry) -> None:
        pass

    @abstractmethod
    async def update_model_version(self, model: ModelRegistry) -> None:
        pass

    @abstractmethod
    async def get_model_version(self, model_version: str) -> Optional[ModelRegistry]:
        pass


class IModelBootstrapService(ABC):
    @abstractmethod
    async def bootstrap_model_v0(
        self,
        models_dir: str,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo: Optional[ITradeHistoryRepository] = None,
    ) -> bool:
        pass


class ICooldownRepository(ABC):
    @abstractmethod
    async def get_cooldown(self, wallet_address: str, token_address: str) -> Optional[CooldownState]:
        pass

    @abstractmethod
    async def set_cooldown(self, cooldown: CooldownState) -> None:
        pass

    @abstractmethod
    async def delete_cooldown(self, wallet_address: str, token_address: str) -> None:
        pass


class IOnchainEventRepository(ABC):
    @abstractmethod
    async def add_event(self, event: OnchainEvent) -> None:
        pass


class IWalletMovementMonitor(ABC):
    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

    @abstractmethod
    def get_event_queue(self) -> asyncio.Queue:
        pass


class IRelevanceFilter(ABC):
    @abstractmethod
    async def process_event(self, event_data: dict) -> None:
        pass


class IFilterLogRepository(ABC):
    @abstractmethod
    async def add_log(self, log: FilterAuditLog) -> None:
        pass

    @abstractmethod
    async def get_logs(self, limit: int = 100) -> List[FilterAuditLog]:
        pass


class ITriggerEngine(ABC):
    @abstractmethod
    async def trigger_event(self, event_data: dict) -> None:
        pass


class ITokenInfoService(ABC):
    @abstractmethod
    async def get_token_info(self, token_address: str) -> dict:
        pass


class IMLPipeline(ABC):
    @abstractmethod
    async def analyze_token(self, token_address: str, wallet_source: str, confidence_boost: bool) -> None:
        pass


class IFeatureExtractor(ABC):
    @abstractmethod
    async def extract_features(self, trigger_event: dict) -> FeatureVector:
        pass


class IXGBoostInferenceEngine(ABC):
    @abstractmethod
    async def run_inference(self, feature_vector: FeatureVector) -> dict:
        pass


class ITokenSafetyService(ABC):
    @abstractmethod
    async def get_safety_info(self, token_address: str) -> dict:
        pass


class ITokenSafetyCheckGate(ABC):
    @abstractmethod
    async def evaluate_safety(self, prediction: PredictionResult, feature_vector: FeatureVector) -> SafetyCheckResult:
        pass


class IHardFilterLogRepository(ABC):
    @abstractmethod
    async def add_hard_filter_log(self, log: HardFilterAuditLog) -> None:
        pass

    @abstractmethod
    async def get_hard_filter_logs(self, limit: int = 100) -> List[HardFilterAuditLog]:
        pass


class IErrorLogRepository(ABC):
    @abstractmethod
    async def log_error(self, error_log: SystemErrorLog) -> None:
        pass

    @abstractmethod
    async def get_error_logs(self, limit: int = 100) -> List[SystemErrorLog]:
        pass



