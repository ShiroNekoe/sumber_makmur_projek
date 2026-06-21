# pyrefly: ignore [missing-import]
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "Sumber Makmur System"
    API_V1_STR: str = "/api/v1"
    
    # RPC and indexing endpoints
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    
    # Target whale wallets to track (Whale Wallet A & B)
    TARGET_WALLETS: List[str] = [
        "WhaleA11111111111111111111111111111111111",
        "WhaleB22222222222222222222222222222222222"
    ]
    
    # ML Pipeline & Decision settings
    CONFIDENCE_THRESHOLD: float = 0.75  # >= 75%
    RETRAIN_CRON_UTC: str = "02:00"      # UTC retrain daily
    
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=".env",
        extra="ignore"
    )

settings = Settings()
