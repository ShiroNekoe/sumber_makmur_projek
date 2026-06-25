import os
from typing import List, Dict, Any, Optional
import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Sumber Makmur System"
    API_V1_STR: str = "/api/v1"
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:5173"]
    
    # RPC and indexing endpoints
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_RPC_FALLBACK_URL: str = "https://api.mainnet-beta.solana.com"
    
    # Target whale wallets to track (Whale Wallet A & B)
    TARGET_WALLETS: List[str] = [
        "Wha1eA11111111111111111111111111111111111",
        "Wha1eB22222222222222222222222222222222222"
    ]
    
    # SQLite Database Configuration
    DATABASE_URL: str = "sqlite:///sumber_makmur.db"
    
    # Secure storage for wallet private key (encrypted file)
    ENCRYPTED_KEYPAIR_PATH: str = "wallet_keypair.enc"
    
    # Dynamic settings loaded from config.yaml
    TRIGGER_WINDOW_MINUTES: int = 5
    TRIGGER_MODE: str = "AND"
    MIN_TOKEN_AGE_MINUTES: int = 60
    MIN_LIQUIDITY_USD: float = 5000.0
    COOLDOWN_SECONDS: int = 3600
    
    CONFIDENCE_THRESHOLD: float = 0.75
    RISK_PCT_PER_TRADE: float = 0.01
    
    TRAILING_TP_TIERS: List[Dict[str, Any]] = [
        {"r_min": 1, "r_max": 2, "trail_pct": None},
        {"r_min": 2, "r_max": 5, "trail_pct": 0.25},
        {"r_min": 5, "r_max": 10, "trail_pct": 0.15},
        {"r_min": 10, "r_max": None, "trail_pct": 0.10}
    ]
    
    LABELING_BUY_BENAR_THRESHOLD_R: float = 3.0
    LABELING_SALAH_THRESHOLD_R: float = -1.0
    
    RETRAIN_SCHEDULE_UTC: str = "02:00"
    RETRAIN_MIN_CLOSED_TRADES_FIRST: int = 100
    RETRAIN_MIN_CLOSED_TRADES_ALT: int = 50
    RETRAIN_MIN_BUY_BENAR_IN_ALT: int = 15
    RETRAIN_ROLLING_WINDOW_DAYS: int = 30
    RETRAIN_ROLLBACK_ACCURACY_DROP_PCT: float = 0.05
    
    KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT: float = 0.05
    KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT: float = 0.10
    
    # Relevance Filter Settings
    DEX_ROUTERS: List[str] = [
        "6EF8rrect3EDQS425286575m1111111111111111",
        "675k1aCcZ1V9et197Y21o5t3A8tFmgm5Rz2845m2u3"
    ]
    CUSTODIAL_EXCHANGES: List[str] = []
    MIN_LP_CHANGE_USD: float = 1000.0
    MIN_SWAP_AMOUNT_USD: float = 10.0
    
    # Safety Check Gate Settings
    SAFETY_MAX_TOP_10_HOLDERS_SHARE: float = 0.20
    SAFETY_REQUIRE_LP_LOCKED: bool = True
    SAFETY_REQUIRE_CONTRACT_VERIFIED: bool = True
    SAFETY_REQUIRE_MINT_AUTHORITY_REVOKED: bool = True
    
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=(".env", "backend/.env"),
        extra="ignore"
    )

    def load_yaml_config(self, yaml_path: str):
        if os.path.exists(yaml_path):
            try:
                with open(yaml_path, 'r') as f:
                    config_data = yaml.safe_load(f)
                    if not config_data:
                        return
                    
                    # trigger_engine
                    te = config_data.get("trigger_engine", {})
                    self.TRIGGER_WINDOW_MINUTES = te.get("window_minutes", self.TRIGGER_WINDOW_MINUTES)
                    self.TRIGGER_MODE = te.get("mode", self.TRIGGER_MODE)
                    self.MIN_TOKEN_AGE_MINUTES = te.get("min_token_age_minutes", self.MIN_TOKEN_AGE_MINUTES)
                    self.MIN_LIQUIDITY_USD = te.get("min_liquidity_usd", self.MIN_LIQUIDITY_USD)
                    self.COOLDOWN_SECONDS = te.get("cooldown_seconds", self.COOLDOWN_SECONDS)
                    
                    # decision_gate
                    dg = config_data.get("decision_gate", {})
                    self.CONFIDENCE_THRESHOLD = dg.get("confidence_threshold", self.CONFIDENCE_THRESHOLD)
                    
                    # risk
                    rk = config_data.get("risk", {})
                    self.RISK_PCT_PER_TRADE = rk.get("risk_pct_per_trade", self.RISK_PCT_PER_TRADE)
                    
                    # trailing_tp
                    tt = config_data.get("trailing_tp", {})
                    self.TRAILING_TP_TIERS = tt.get("tiers", self.TRAILING_TP_TIERS)
                    
                    # labeling
                    lb = config_data.get("labeling", {})
                    self.LABELING_BUY_BENAR_THRESHOLD_R = lb.get("buy_benar_threshold_r", self.LABELING_BUY_BENAR_THRESHOLD_R)
                    self.LABELING_SALAH_THRESHOLD_R = lb.get("salah_threshold_r", self.LABELING_SALAH_THRESHOLD_R)
                    
                    # retrain
                    rt = config_data.get("retrain", {})
                    self.RETRAIN_SCHEDULE_UTC = rt.get("schedule_utc", self.RETRAIN_SCHEDULE_UTC)
                    self.RETRAIN_MIN_CLOSED_TRADES_FIRST = rt.get("min_closed_trades_first", self.RETRAIN_MIN_CLOSED_TRADES_FIRST)
                    self.RETRAIN_MIN_CLOSED_TRADES_ALT = rt.get("min_closed_trades_alt", self.RETRAIN_MIN_CLOSED_TRADES_ALT)
                    self.RETRAIN_MIN_BUY_BENAR_IN_ALT = rt.get("min_buy_benar_in_alt", self.RETRAIN_MIN_BUY_BENAR_IN_ALT)
                    self.RETRAIN_ROLLING_WINDOW_DAYS = rt.get("rolling_window_days", self.RETRAIN_ROLLING_WINDOW_DAYS)
                    self.RETRAIN_ROLLBACK_ACCURACY_DROP_PCT = rt.get("rollback_accuracy_drop_pct", self.RETRAIN_ROLLBACK_ACCURACY_DROP_PCT)
                    
                    # kill_switch
                    ks = config_data.get("kill_switch", {})
                    self.KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT = ks.get("dev_wallet_sell_threshold_pct", self.KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT)
                    self.KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT = ks.get("slippage_spike_threshold_pct", self.KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT)
                    
                    # relevance_filter
                    rf = config_data.get("relevance_filter", {})
                    self.MIN_SWAP_AMOUNT_USD = rf.get("min_swap_amount_usd", self.MIN_SWAP_AMOUNT_USD)
                    self.MIN_LP_CHANGE_USD = rf.get("min_lp_change_usd", self.MIN_LP_CHANGE_USD)
                    self.DEX_ROUTERS = rf.get("dex_routers", self.DEX_ROUTERS)
                    self.CUSTODIAL_EXCHANGES = rf.get("custodial_exchanges", self.CUSTODIAL_EXCHANGES)
                    
                    # safety_check
                    sc = config_data.get("safety_check", {})
                    self.SAFETY_MAX_TOP_10_HOLDERS_SHARE = sc.get("max_top_10_holders_share", self.SAFETY_MAX_TOP_10_HOLDERS_SHARE)
                    self.SAFETY_REQUIRE_LP_LOCKED = sc.get("require_lp_locked", self.SAFETY_REQUIRE_LP_LOCKED)
                    self.SAFETY_REQUIRE_CONTRACT_VERIFIED = sc.get("require_contract_verified", self.SAFETY_REQUIRE_CONTRACT_VERIFIED)
                    self.SAFETY_REQUIRE_MINT_AUTHORITY_REVOKED = sc.get("require_mint_authority_revoked", self.SAFETY_REQUIRE_MINT_AUTHORITY_REVOKED)
            except Exception as e:
                print(f"Error loading YAML config from {yaml_path}: {e}")


settings = Settings()

# Try loading from multiple paths
yaml_paths = [
    "config.yaml",
    "backend/config.yaml",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.yaml"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
]

for path in yaml_paths:
    if os.path.exists(path):
        settings.load_yaml_config(path)
        break
