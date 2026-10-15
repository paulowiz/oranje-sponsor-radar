import argparse
import logging
import os
from pathlib import Path

from radar.db import check_dataset, make_engine
from radar.demo import seed_demo
from radar.enrichment import sync_enrichment
from radar.pipeline import refresh
from radar.raw import export_raw, initialize_database


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "refresh", "seed-demo", "export-raw", "enrich"])
    parser.add_argument("--kvk", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("config") / "enrichment-report.json")
    args = parser.parse_args()
    engine = make_engine()
    if args.command == "init":
        initialize_database(engine)
        check_dataset(engine)
    elif args.command == "seed-demo":
        seed_demo(engine)
    elif args.command == "export-raw":
        logging.info("Exported raw batch %s", export_raw(engine))
    elif args.command == "enrich":
        report = sync_enrichment(
            engine,
            args.kvk,
            Path(os.getenv("COMPANIES_FILE", str(Path("config") / "companies.json"))),
            args.report,
            args.apply,
        )
        logging.info("Enrichment report saved to %s", args.report)
        if report["errors"]:
            raise SystemExit(1)
    elif not refresh(
        engine, Path(os.getenv("COMPANIES_FILE", str(Path("config") / "companies.json")))
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
