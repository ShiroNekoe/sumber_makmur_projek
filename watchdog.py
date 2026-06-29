import subprocess
import time
import sys
import os
import logging
from datetime import datetime, timezone

# Configure logging for the watchdog process
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (Watchdog) %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("watchdog.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("watchdog")

def main():
    restart_count = 0
    # Command to run backend
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
    
    logger.info("Starting Watchdog process monitoring Solana AI Trading System...")
    
    # Path of backend directory
    backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
    
    while True:
        logger.info(f"Launching main process: {' '.join(cmd)}")
        start_time = time.time()
        
        try:
            # Launch the backend process
            process = subprocess.Popen(
                cmd,
                stdout=sys.stdout,
                stderr=sys.stderr,
                cwd=backend_dir
            )
            
            # Wait for process termination
            exit_code = process.wait()
            
            uptime = time.time() - start_time
            crash_time = datetime.now(timezone.utc).isoformat()
            
            if exit_code != 0:
                restart_count += 1
                logger.error(
                    f"[CRASH DETECTED] Main process exited with code {exit_code}.\n"
                    f" - Crash Time: {crash_time}\n"
                    f" - Process Uptime: {uptime:.1f} seconds\n"
                    f" - Restart Count: {restart_count}\n"
                    f" - Delaying restart by 10 seconds..."
                )
                time.sleep(10.0)
            else:
                logger.info(f"Main process exited cleanly with code 0. Exiting watchdog.")
                break
                
        except KeyboardInterrupt:
            logger.info("Watchdog terminated by user (KeyboardInterrupt).")
            if 'process' in locals():
                process.terminate()
            break
        except Exception as e:
            restart_count += 1
            logger.exception(f"Exception in watchdog process runner: {e}")
            time.sleep(10.0)

if __name__ == "__main__":
    main()
