import os
from dotenv import load_dotenv
load_dotenv()
load_dotenv("backend/.env")
import yaml
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    PROJECT_NAME: str = "Sumber Makmur System"
    API_V1_STR: str = "/api/v1"
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:5174"]
    
    # RPC and indexing endpoints
    SOLANA_RPC_PRIMARY_URL: Optional[str] = None
    SOLANA_RPC_SECONDARY_URL: Optional[str] = None
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_RPC_FALLBACK_URL: str = "https://api.mainnet-beta.solana.com"
    
    RPC_PRIMARY_URL: str = "https://api.mainnet-beta.solana.com"
    RPC_SECONDARY_URL: str = "https://api.mainnet-beta.solana.com"
    RPC_MAX_RETRY: int = 5
    
    # Target whale wallets to track (Active on-chain Solana wallets)
    TARGET_WALLETS: List[str] = [
        "JD6rVaerbyz6wjQ433nrw6bFTgFrp46MiYmi8EtUAfsG",
        "Fw6Tgm8uCKb35GPwsjhKv6LTFq3m6L1U35hHPYR8Ai3C",
        "4jjEXcFPXw7WVGXSTb227HW6wfLprjh2RtiHty4GbetE",
        "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
    ]

    
    # SQLite Database Configuration (Always resolve absolutely to the workspace root)
    DATABASE_URL: str = "sqlite:///" + os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "sumber_makmur.db")
    ).replace("\\", "/")

    
    # Secure storage for wallet private key (encrypted file)
    ENCRYPTED_KEYPAIR_PATH: str = "wallet_keypair.enc"
    
    # Dynamic settings loaded from config.yaml
    TRIGGER_WINDOW_MINUTES: int = 5
    TRIGGER_MODE: str = "AND"
    MIN_TOKEN_AGE_MINUTES: float = 2.0
    MAX_TOKEN_AGE_MINUTES: float = 30.0
    MIN_LIQUIDITY_USD: float = 3000.0
    COOLDOWN_SECONDS: int = 3600
    
    CONFIDENCE_THRESHOLD: float = 0.75
    RISK_PCT_PER_TRADE: float = 0.01
    RISK_MAX_CONCURRENT_POSITIONS: int = 3
    RISK_MAX_DAILY_LOSS_PCT: float = 0.05
    RISK_MAX_WEEKLY_LOSS_PCT: float = 0.15
    RISK_MAX_TOTAL_EXPOSURE_USD: float = 2500.0
    RISK_CIRCUIT_BREAKER_RESET_UTC: str = "00:00"
    
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

    # Model v0 Bootstrap Settings
    BOOTSTRAP_HISTORY_DAYS: int = 7
    BOOTSTRAP_MIN_TRADES_WARNING: int = 10
    BOOTSTRAP_MAX_SIGNATURES_PER_WALLET: int = 60
    BOOTSTRAP_API_TIMEOUT_SECONDS: float = 8.0
    
    KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT: float = 0.05
    KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT: float = 0.10

    # Transaction fee settings (tiered by urgency)
    PRIORITY_FEE_BUY: float = 0.0001      # SOL — BUY: cukup kompetitif
    PRIORITY_FEE_SELL: float = 0.00005    # SOL — SELL normal: hemat
    PRIORITY_FEE_DUST: float = 0.000005   # SOL — dust/close: hampir gratis
    SLIPPAGE_BUY_PCT: float = 5.0         # % slippage beli
    SLIPPAGE_SELL_PCT: float = 10.0       # % slippage jual normal
    SLIPPAGE_SELL_EMERGENCY_PCT: float = 25.0  # % slippage emergency/kill-switch

    # Dynamic Wallet Discovery Settings
    DISCOVERY_OCCURRENCE_THRESHOLD: int = 3
    DISCOVERY_PROFIT_VERIFICATION_MIN_PCT: float = 0.60
    DISCOVERY_TRADE_ENABLED: bool = True
    
    # Coin Blacklist settings
    BLACKLIST_KEYWORDS: List[str] = ["PEPE", "BONK", "DOGE", "FARTCOIN", "FART", "SHIB", "FLOKI", "WIF"]
    BLACKLIST_MINTS: List[str] = [
        "DezXAZ8z7PnrnRJjz3wXBoRgixrfNg7yFLBnRx4S75Jb", # BONK
        "9b3j5dg64BDm18mC69o1zM45p1LsNz29o2FDN26Dpump", # Fartcoin
    ]
    
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
    SAFETY_MAX_DEPLOYER_HOLDING_PCT: float = 0.10

    # Config Versioning Info
    CONFIG_VERSION_TIMESTAMP: str = datetime.now(timezone.utc).isoformat()
    CONFIG_FILE_PATH: Optional[str] = None
    
    # Real Wallet Credentials & Exchange API Keys
    SOLANA_WALLET_PUBLIC_KEY: Optional[str] = None
    SOLANA_WALLET_PRIVATE_KEY: Optional[str] = None
    PUMP_FUN_API_KEY: Optional[str] = None
    
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=(".env", "backend/.env"),
        extra="ignore"
    )

    def validate_config(self, config_data: dict):
        """Validates structured types, value bounds, and constraints of config.yaml."""
        required_structure = {
            "trigger_engine": ["window_minutes", "mode", "min_token_age_minutes", "max_token_age_minutes", "min_liquidity_usd", "cooldown_seconds"],
            "decision_gate": ["confidence_threshold"],
            "risk": ["risk_pct_per_trade", "max_concurrent_positions"],
            "trailing_tp": ["tiers"],
            "labeling": ["buy_benar_threshold_r", "salah_threshold_r"],
            "retrain": ["schedule_utc", "min_closed_trades_first", "min_closed_trades_alt", "min_buy_benar_in_alt", "rolling_window_days", "rollback_accuracy_drop_pct"],
            "kill_switch": ["dev_wallet_sell_threshold_pct", "slippage_spike_threshold_pct"],
            "rpc": ["primary_url", "secondary_url", "max_retry"]
        }
        
        missing_fields = []
        for section, fields in required_structure.items():
            if section not in config_data:
                missing_fields.append(f"section: '{section}'")
                continue
            sec_data = config_data[section] or {}
            for field in fields:
                if field not in sec_data:
                    missing_fields.append(f"'{section}.{field}'")
                    
        if missing_fields:
            raise ValueError(f"Required config fields are missing: {', '.join(missing_fields)}")
            
        # Range/type validations
        te = config_data.get("trigger_engine", {})
        window_minutes = te.get("window_minutes")
        if not isinstance(window_minutes, (int, float)) or window_minutes <= 0:
            raise ValueError("trigger_engine.window_minutes must be a number > 0")
        if te.get("mode") not in ["AND", "OR"]:
            raise ValueError("trigger_engine.mode must be either 'AND' or 'OR'")
            
        dg = config_data.get("decision_gate", {})
        conf_thresh = dg.get("confidence_threshold")
        if not isinstance(conf_thresh, (int, float)) or not (0.0 <= conf_thresh <= 1.0):
            raise ValueError("decision_gate.confidence_threshold must be a number between 0.0 and 1.0")
            
        rk = config_data.get("risk", {})
        risk_pct = rk.get("risk_pct_per_trade")
        if not isinstance(risk_pct, (int, float)) or not (0.0 < risk_pct <= 1.0):
            raise ValueError("risk.risk_pct_per_trade must be a number between 0.0 and 1.0")
        max_pos = rk.get("max_concurrent_positions")
        if not isinstance(max_pos, int) or max_pos <= 0:
            raise ValueError("risk.max_concurrent_positions must be an integer > 0")
            
        # Trailing TP tiers validation
        tt = config_data.get("trailing_tp", {})
        tiers = tt.get("tiers")
        if not isinstance(tiers, list):
            raise ValueError("trailing_tp.tiers must be a list")
            
        last_r_max = 0.0
        for idx, tier in enumerate(tiers):
            r_min = tier.get("r_min")
            r_max = tier.get("r_max")
            
            if not isinstance(r_min, (int, float)):
                raise ValueError(f"trailing_tp tier {idx}: r_min must be a number")
                
            if r_max is not None and not isinstance(r_max, (int, float)):
                raise ValueError(f"trailing_tp tier {idx}: r_max must be a number or null")
                
            if r_min < last_r_max:
                raise ValueError(f"trailing_tp tier {idx}: r_min ({r_min}) overlaps with previous r_max ({last_r_max})")
                
            if r_max is not None:
                if r_max <= r_min:
                    raise ValueError(f"trailing_tp tier {idx}: r_max ({r_max}) must be greater than r_min ({r_min})")
                last_r_max = r_max
            else:
                if idx != len(tiers) - 1:
                    raise ValueError(f"trailing_tp tier {idx}: null r_max is only allowed on the last tier")

        # RPC URL format validations
        rpc = config_data.get("rpc", {})
        for url_key in ["primary_url", "secondary_url"]:
            url = rpc.get(url_key)
            if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://") or url.startswith("ws://") or url.startswith("wss://") or url.startswith("${") or url == ""):
                raise ValueError(f"rpc.{url_key} must be a valid HTTP/HTTPS or WS/WSS URL (got: '{url}')")

    def apply_config(self, config_data: dict, hot_reload: bool = False):
        """Applies configuration fields to settings attributes with hot-reload safety rules."""
        self.validate_config(config_data)

        # Trigger Engine
        te = config_data.get("trigger_engine", {})
        self.TRIGGER_WINDOW_MINUTES = te.get("window_minutes", self.TRIGGER_WINDOW_MINUTES)
        self.TRIGGER_MODE = te.get("mode", self.TRIGGER_MODE)
        self.MIN_TOKEN_AGE_MINUTES = te.get("min_token_age_minutes", self.MIN_TOKEN_AGE_MINUTES)
        self.MAX_TOKEN_AGE_MINUTES = te.get("max_token_age_minutes", self.MAX_TOKEN_AGE_MINUTES)
        self.MIN_LIQUIDITY_USD = te.get("min_liquidity_usd", self.MIN_LIQUIDITY_USD)
        self.COOLDOWN_SECONDS = te.get("cooldown_seconds", self.COOLDOWN_SECONDS)
        
        # Decision Gate
        dg = config_data.get("decision_gate", {})
        self.CONFIDENCE_THRESHOLD = dg.get("confidence_threshold", self.CONFIDENCE_THRESHOLD)
        
        # Risk
        rk = config_data.get("risk", {})
        self.RISK_PCT_PER_TRADE = rk.get("risk_pct_per_trade", self.RISK_PCT_PER_TRADE)
        self.RISK_MAX_CONCURRENT_POSITIONS = rk.get("max_concurrent_positions", self.RISK_MAX_CONCURRENT_POSITIONS)
        self.RISK_MAX_DAILY_LOSS_PCT = rk.get("max_daily_loss_pct", self.RISK_MAX_DAILY_LOSS_PCT)
        self.RISK_MAX_WEEKLY_LOSS_PCT = rk.get("max_weekly_loss_pct", self.RISK_MAX_WEEKLY_LOSS_PCT)
        self.RISK_MAX_TOTAL_EXPOSURE_USD = rk.get("max_total_exposure_usd", self.RISK_MAX_TOTAL_EXPOSURE_USD)
        self.RISK_CIRCUIT_BREAKER_RESET_UTC = rk.get("circuit_breaker_reset_utc", self.RISK_CIRCUIT_BREAKER_RESET_UTC)
        
        # Trailing TP
        tt = config_data.get("trailing_tp", {})
        self.TRAILING_TP_TIERS = tt.get("tiers", self.TRAILING_TP_TIERS)
        
        # Labeling
        lb = config_data.get("labeling", {})
        self.LABELING_BUY_BENAR_THRESHOLD_R = lb.get("buy_benar_threshold_r", self.LABELING_BUY_BENAR_THRESHOLD_R)
        self.LABELING_SALAH_THRESHOLD_R = lb.get("salah_threshold_r", self.LABELING_SALAH_THRESHOLD_R)
        
        # Retrain
        rt = config_data.get("retrain", {})
        self.RETRAIN_SCHEDULE_UTC = rt.get("schedule_utc", self.RETRAIN_SCHEDULE_UTC)
        self.RETRAIN_MIN_CLOSED_TRADES_FIRST = rt.get("min_closed_trades_first", self.RETRAIN_MIN_CLOSED_TRADES_FIRST)
        self.RETRAIN_MIN_CLOSED_TRADES_ALT = rt.get("min_closed_trades_alt", self.RETRAIN_MIN_CLOSED_TRADES_ALT)
        self.RETRAIN_MIN_BUY_BENAR_IN_ALT = rt.get("min_buy_benar_in_alt", self.RETRAIN_MIN_BUY_BENAR_IN_ALT)
        self.RETRAIN_ROLLING_WINDOW_DAYS = rt.get("rolling_window_days", self.RETRAIN_ROLLING_WINDOW_DAYS)
        self.RETRAIN_ROLLBACK_ACCURACY_DROP_PCT = rt.get("rollback_accuracy_drop_pct", self.RETRAIN_ROLLBACK_ACCURACY_DROP_PCT)

        # Model Bootstrap
        mb = config_data.get("model_bootstrap", {})
        self.BOOTSTRAP_HISTORY_DAYS = mb.get("history_days", self.BOOTSTRAP_HISTORY_DAYS)
        self.BOOTSTRAP_MIN_TRADES_WARNING = mb.get("min_trades_warning", self.BOOTSTRAP_MIN_TRADES_WARNING)
        self.BOOTSTRAP_MAX_SIGNATURES_PER_WALLET = mb.get("max_signatures_per_wallet", self.BOOTSTRAP_MAX_SIGNATURES_PER_WALLET)
        self.BOOTSTRAP_API_TIMEOUT_SECONDS = mb.get("api_timeout_seconds", self.BOOTSTRAP_API_TIMEOUT_SECONDS)
        
        # Kill Switch
        ks = config_data.get("kill_switch", {})
        self.KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT = ks.get("dev_wallet_sell_threshold_pct", self.KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT)
        self.KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT = ks.get("slippage_spike_threshold_pct", self.KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT)

        # Transaction Fees (tiered)
        tf = config_data.get("transaction_fees", {})
        self.PRIORITY_FEE_BUY = tf.get("priority_fee_buy", self.PRIORITY_FEE_BUY)
        self.PRIORITY_FEE_SELL = tf.get("priority_fee_sell", self.PRIORITY_FEE_SELL)
        self.PRIORITY_FEE_DUST = tf.get("priority_fee_dust", self.PRIORITY_FEE_DUST)
        self.SLIPPAGE_BUY_PCT = tf.get("slippage_buy_pct", self.SLIPPAGE_BUY_PCT)
        self.SLIPPAGE_SELL_PCT = tf.get("slippage_sell_pct", self.SLIPPAGE_SELL_PCT)
        self.SLIPPAGE_SELL_EMERGENCY_PCT = tf.get("slippage_sell_emergency_pct", self.SLIPPAGE_SELL_EMERGENCY_PCT)
        
        # Discovery
        dy = config_data.get("discovery", {})
        self.DISCOVERY_OCCURRENCE_THRESHOLD = dy.get("occurrence_threshold", self.DISCOVERY_OCCURRENCE_THRESHOLD)
        self.DISCOVERY_PROFIT_VERIFICATION_MIN_PCT = dy.get("profit_verification_min_pct", self.DISCOVERY_PROFIT_VERIFICATION_MIN_PCT)
        self.DISCOVERY_TRADE_ENABLED = dy.get("trade_enabled", self.DISCOVERY_TRADE_ENABLED)

        # Relevance Filter
        rf = config_data.get("relevance_filter", {})
        self.MIN_SWAP_AMOUNT_USD = rf.get("min_swap_amount_usd", self.MIN_SWAP_AMOUNT_USD)
        self.MIN_LP_CHANGE_USD = rf.get("min_lp_change_usd", self.MIN_LP_CHANGE_USD)
        self.DEX_ROUTERS = rf.get("dex_routers", self.DEX_ROUTERS)
        self.CUSTODIAL_EXCHANGES = rf.get("custodial_exchanges", self.CUSTODIAL_EXCHANGES)
        
        # Safety Check Gate
        sc = config_data.get("safety_check", {})
        self.SAFETY_MAX_TOP_10_HOLDERS_SHARE = sc.get("max_top_10_holders_share", self.SAFETY_MAX_TOP_10_HOLDERS_SHARE)
        self.SAFETY_REQUIRE_LP_LOCKED = sc.get("require_lp_locked", self.SAFETY_REQUIRE_LP_LOCKED)
        self.SAFETY_REQUIRE_CONTRACT_VERIFIED = sc.get("require_contract_verified", self.SAFETY_REQUIRE_CONTRACT_VERIFIED)
        self.SAFETY_REQUIRE_MINT_AUTHORITY_REVOKED = sc.get("require_mint_authority_revoked", self.SAFETY_REQUIRE_MINT_AUTHORITY_REVOKED)
        self.SAFETY_MAX_DEPLOYER_HOLDING_PCT = sc.get("max_deployer_holding_pct", self.SAFETY_MAX_DEPLOYER_HOLDING_PCT)

        # Blacklist
        bl = config_data.get("blacklist", {})
        self.BLACKLIST_KEYWORDS = bl.get("keywords", self.BLACKLIST_KEYWORDS)
        self.BLACKLIST_MINTS = bl.get("mints", self.BLACKLIST_MINTS)

        # RPC Parameters Validation (Restart Needed Warn for Hot-Reload)
        rp = config_data.get("rpc", {})
        if not hot_reload:
            env_primary = os.getenv("SOLANA_RPC_PRIMARY_URL") or os.getenv("RPC_PRIMARY_URL") or os.getenv("SOLANA_RPC_URL")
            if env_primary and env_primary.strip() and not env_primary.startswith("${"):
                self.RPC_PRIMARY_URL = env_primary
            else:
                yaml_primary = rp.get("primary_url")
                if yaml_primary and yaml_primary.strip() and not yaml_primary.startswith("${"):
                    self.RPC_PRIMARY_URL = yaml_primary
                    logger.warning("[SECURITY WARNING] Reading RPC Primary URL from config.yaml fallback. Please configure SOLANA_RPC_PRIMARY_URL in .env instead.")
                else:
                    self.RPC_PRIMARY_URL = "https://api.mainnet-beta.solana.com"

            env_secondary = os.getenv("SOLANA_RPC_SECONDARY_URL") or os.getenv("RPC_SECONDARY_URL") or os.getenv("SOLANA_RPC_FALLBACK_URL")
            if env_secondary and env_secondary.strip() and not env_secondary.startswith("${"):
                self.RPC_SECONDARY_URL = env_secondary
            else:
                yaml_secondary = rp.get("secondary_url")
                if yaml_secondary and yaml_secondary.strip() and not yaml_secondary.startswith("${"):
                    self.RPC_SECONDARY_URL = yaml_secondary
                    logger.warning("[SECURITY WARNING] Reading RPC Secondary URL from config.yaml fallback. Please configure SOLANA_RPC_SECONDARY_URL in .env instead.")
                else:
                    self.RPC_SECONDARY_URL = "https://api.mainnet-beta.solana.com"

            self.RPC_MAX_RETRY = rp.get("max_retry", self.RPC_MAX_RETRY)
            self.SOLANA_RPC_URL = self.RPC_PRIMARY_URL
            self.SOLANA_RPC_FALLBACK_URL = self.RPC_SECONDARY_URL
        else:
            if (rp.get("primary_url") != self.RPC_PRIMARY_URL or 
                rp.get("secondary_url") != self.RPC_SECONDARY_URL or 
                rp.get("max_retry") != self.RPC_MAX_RETRY):
                logger.warning("[CONFIG HOT-RELOAD] Critical RPC URL changes detected in config.yaml. These parameters require a process restart to take effect.")

    def load_yaml_config(self, yaml_path: str):
        """Loads and processes config data from a config.yaml file path."""
        if os.path.exists(yaml_path):
            with open(yaml_path, 'r') as f:
                config_data = yaml.safe_load(f)
                if config_data:
                    self.apply_config(config_data, hot_reload=False)
                    self.CONFIG_FILE_PATH = os.path.abspath(yaml_path)
                    self.CONFIG_VERSION_TIMESTAMP = datetime.now(timezone.utc).isoformat()
                    logger.info(f"Loaded config successfully from {yaml_path}. Version: {self.CONFIG_VERSION_TIMESTAMP}")

    async def watch_config_loop(self):
        """Asynchronous background loop to watch config.yaml for modifications and hot-reload."""
        if not self.CONFIG_FILE_PATH or not os.path.exists(self.CONFIG_FILE_PATH):
            logger.warning("[CONFIG WATCH] No valid config file path to watch.")
            return

        last_mtime = os.path.getmtime(self.CONFIG_FILE_PATH)
        logger.info(f"[CONFIG WATCH] Watching {self.CONFIG_FILE_PATH} for modifications.")

        while True:
            await asyncio.sleep(2.0)
            if not os.path.exists(self.CONFIG_FILE_PATH):
                continue
            
            try:
                current_mtime = os.path.getmtime(self.CONFIG_FILE_PATH)
                if current_mtime > last_mtime:
                    logger.info(f"[CONFIG WATCH] File modification detected. Hot-reloading...")
                    with open(self.CONFIG_FILE_PATH, 'r') as f:
                        config_data = yaml.safe_load(f)
                    
                    self.apply_config(config_data, hot_reload=True)
                    self.CONFIG_VERSION_TIMESTAMP = datetime.now(timezone.utc).isoformat()
                    last_mtime = current_mtime
                    logger.info(f"[CONFIG WATCH] Hot-reload complete. Version: {self.CONFIG_VERSION_TIMESTAMP}")
                    
                    # Notify dashboard
                    from app.websocket.manager import manager as ws_manager
                    await ws_manager.broadcast({
                        "type": "system_alert",
                        "data": {
                            "event": "system_alert",
                            "alert_type": "config_reload",
                            "message": f"Config file hot-reloaded successfully. Version: {self.CONFIG_VERSION_TIMESTAMP}",
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                    })
            except Exception as e:
                logger.error(f"[CONFIG WATCH] Hot-reload error: {e}", exc_info=True)
                # Keep previous settings, notify dashboard of error
                from app.websocket.manager import manager as ws_manager
                await ws_manager.broadcast({
                    "type": "system_alert",
                    "data": {
                        "event": "system_alert",
                        "alert_type": "config_reload_error",
                        "message": f"ERROR: Failed to hot-reload config: {str(e)}. Previous config kept.",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })


settings = Settings()

# Try loading from multiple paths
yaml_paths = [
    "config.yaml",
    "backend/config.yaml",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.yaml"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
]

config_loaded = False
for path in yaml_paths:
    if os.path.exists(path):
        try:
            settings.load_yaml_config(path)
            config_loaded = True
            break
        except Exception as e:
            logger.critical(f"FATAL CONFIG ERROR: {e}")
            raise SystemExit(f"CRITICAL: Failed to load config.yaml: {e}. System halted.")

if not config_loaded:
    logger.critical("FATAL: config.yaml not found on startup.")
    instructions = (
        "\n========================================================================\n"
        "CRITICAL ERROR: config.yaml not found!\n"
        "Please create a config.yaml file in the root of the workspace directory.\n"
        "========================================================================\n"
    )
    print(instructions)
    raise SystemExit("config.yaml is missing. Halt.")
