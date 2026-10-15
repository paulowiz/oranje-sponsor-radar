import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


def run_cycle() -> bool:
    result = subprocess.run([sys.executable, "-m", "radar.cli", "refresh"], check=False)
    if result.returncode:
        logger.error(
            "Refresh failed or was partial; skipping analytics (exit %s)", result.returncode
        )
        return False
    result = subprocess.run(
        ["dbt", "build", "--project-dir", "/app/analytics", "--profiles-dir", "/app/analytics"],
        check=False,
    )
    if result.returncode:
        logger.error("dbt build failed (exit %s)", result.returncode)
        return False
    logger.info("Refresh and analytics completed")
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    hour = int(os.getenv("REFRESH_HOUR_UTC", "4"))
    if not 0 <= hour <= 23:
        raise ValueError("REFRESH_HOUR_UTC must be in [0, 23]")
    # Run on startup, then at the configured UTC hour. No concurrent background children.
    while True:
        run_cycle()
        now = datetime.now(UTC)
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        logger.info("Next refresh: %s", next_run.isoformat())
        time.sleep((next_run - now).total_seconds())


if __name__ == "__main__":
    main()
