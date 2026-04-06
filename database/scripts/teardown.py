#!/usr/bin/env python3
# Teardown script – drops all database tables and optionally deletes the DB file.
#
# Usage:
#   python teardown.py            – drop all tables, keep the .db file
#   python teardown.py --delete   – drop all tables AND delete the .db file

import sys
import logging
from pathlib import Path

# Ensure the project root is on sys.path so 'database' package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from database.alchemy.database import teardown_db, DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    delete_file = "--delete" in sys.argv

    warning = f"This will DROP all tables in '{DB_PATH}'"
    if delete_file:
        warning += " and DELETE the file"
    warning += ".\nType 'yes' to continue: "

    answer = input(warning)
    if answer.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    teardown_db(delete_file=delete_file)
    logger.info("Teardown complete.")


if __name__ == "__main__":
    main()
