"""Store a private local development note; never embed it in public docs."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from nightdesk.config import LOGS
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text")
    args = parser.parse_args()
    with (LOGS / "private-notes.jsonl").open("a") as handle:
        handle.write(json.dumps({"at": time.time(), "text": args.text}) + "\n")
    print("Saved a local private note.")
