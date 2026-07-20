import logging
import re
import sys

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


class ColoredFormatter(logging.Formatter):
    """Custom logging formatter to style and colorize terminal telemetry."""
    def format(self, record):
        time_str = f"{BLUE}{self.formatTime(record, '%H:%M:%S')}{RESET}"
        
        # Colorize levels and prepend emojis
        if record.levelno == logging.WARNING:
            level_str = f"{YELLOW}[WARNING] ⚠️{RESET}"
        elif record.levelno >= logging.ERROR:
            level_str = f"{RED}[ERROR] ❌{RESET}"
        else:
            level_str = f"{GREEN}[INFO] ℹ️{RESET}"

        msg = record.getMessage()

        # Regex replace for system component tags
        msg = re.sub(r"\[TRIGGER ENGINE\]", f"{CYAN}[TRIGGER ENGINE] ⚙️{RESET}", msg)
        msg = re.sub(r"\[RELEVANCE FILTER\]", f"{CYAN}[RELEVANCE FILTER] 🔍{RESET}", msg)
        msg = re.sub(r"\[SAFETY GATE\]", f"{MAGENTA}[SAFETY GATE] 🛡️{RESET}", msg)
        msg = re.sub(r"\[ML PIPELINE\]", f"{MAGENTA}[ML PIPELINE] 🚀{RESET}", msg)
        msg = re.sub(r"\[XGBOOST ENGINE\]", f"{MAGENTA}[XGBOOST ENGINE] 🤖{RESET}", msg)
        msg = re.sub(r"\[PROTECTION\]", f"{RED}[PROTECTION] 🔒{RESET}", msg)
        msg = re.sub(r"\[AUTO TRADE\]", f"{GREEN}[AUTO TRADE] 💸{RESET}", msg)
        msg = re.sub(r"\[SIMULATOR\]", f"{YELLOW}[SIMULATOR] 🔮{RESET}", msg)
        msg = re.sub(r"\[TOKEN SERVICE\]", f"{CYAN}[TOKEN SERVICE] 🌐{RESET}", msg)
        msg = re.sub(r"\[FEATURE EXTRACTOR\]", f"{CYAN}[FEATURE EXTRACTOR] 📋{RESET}", msg)
        msg = re.sub(r"\[XGBOOST INFERENCE\]", f"{MAGENTA}[XGBOOST INFERENCE] 🧠{RESET}", msg)
        msg = re.sub(r"\[HARD FILTER\]", f"{CYAN}[HARD FILTER] 🛡️{RESET}", msg)
        msg = re.sub(r"\[SCAN\]", f"{MAGENTA}{BOLD}[SCAN] 🔍{RESET}", msg)
        msg = re.sub(r"\[AGE FILTER\]", f"{YELLOW}{BOLD}[AGE FILTER] ⏳{RESET}", msg)

        # Highlight whale names, amounts, and actions
        msg = re.sub(r"(Whale[A-Za-z0-9]+|Wha1e[A-Za-z0-9]+)", f"{YELLOW}\\1{RESET}", msg)
        msg = re.sub(r"(\$[0-9\.,]+)", f"{GREEN}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"(passed|PASSED|PASS|success|confirmed|CONFIRMED|FIRED)", f"{GREEN}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"(failed|FAILED|blocked|BLOCKED|timeout|TIMEOUT)", f"{RED}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"(skip|SKIP)", f"{YELLOW}{BOLD}\\1{RESET}", msg)
        
        # Colorize tokens addresses
        msg = re.sub(r"([1-9A-HJ-NP-Za-km-z]{6}\.\.\.[1-9A-HJ-NP-Za-km-z]{4}|[1-9A-HJ-NP-Za-km-z]{32,44})", f"{CYAN}\\1{RESET}", msg)

        return f"{time_str} {level_str} {msg}"


def setup_terminal_logging():
    """Safely configures the root logger with the ColoredFormatter, avoiding double-attachment."""
    # Ensure stdout/stderr encoding is UTF-8 on Windows
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    root_logger = logging.getLogger()
    
    # Check if we already have a StreamHandler with ColoredFormatter
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and isinstance(handler.formatter, ColoredFormatter):
            return

    root_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColoredFormatter())
    root_logger.addHandler(handler)
