import json
import logging
import time

from alignment_engine import AlignmentEngine

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/alignment_monitor.log"),
        logging.StreamHandler()
    ]
)

MANIFESTO_PATH = "core/intent/manifesto.json"
CHARTER_PATH = "core/intent/charter.json"
WATCHDOG_LOG_PATH = "logs/watchdog.log"
CHECK_INTERVAL_SECONDS = 30

def main():
    logging.info("Alignment Monitor started.")
    
    try:
        engine = AlignmentEngine(
            MANIFESTO_PATH,
            CHARTER_PATH,
            WATCHDOG_LOG_PATH
        )
        
        while True:
            report = engine.get_report()
            score = report['alignment_score']
            status = report['status']
            
            log_msg = f"Alignment Score: {score} | Status: {status}"
            
            if score < 0.5:
                logging.error(f"🚨 {log_msg}")
            elif score < 0.8:
                logging.warning(f"⚠️ {log_msg}")
            else:
                logging.info(f"✅ {log_msg}")
                
            # Also write to a dedicated telemetry file for easy reading
            with open("services/telemetry/alignment_score.json", "w") as f:
                json.dump(report, f, indent=2)
                
            time.sleep(CHECK_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        logging.info("Alignment Monitor stopped by user.")
    except Exception as e:
        logging.error(f"Alignment Monitor encountered a fatal error: {e}")

if __name__ == "__main__":
    main()