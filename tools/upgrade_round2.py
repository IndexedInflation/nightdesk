"""One-off steps for the 14 October 2026 rebuild. Safe to run twice. Run it once the NEW engine is up.

    .venv/bin/python tools/upgrade_round2.py

1. Moves the owner's risk dials from the old default (Cautious) to the new one, but only if he never changed them.
   Done through the engine itself, so the running engine and the saved file cannot disagree.
2. Removes last night's self-check result, which compared against a half-day price file, and says so in the log.
3. Keeps the speed measured in the first live session, so the app can show it while the market is shut.
4. Leaves a note in the activity log saying what changed.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from nightdesk import config                    # noqa: E402
from nightdesk.store import Journal             # noqa: E402

BASE = f"http://127.0.0.1:{config.PORT}"


def call(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Nightdesk": "1"})
    return json.load(urllib.request.urlopen(req, timeout=5))


done = []
try:
    now = call("/api/settings")
    old, new = config.RISK_MODES["cautious"], config.RISK_MODES[config.DEFAULT_MODE]
    if "modes" not in now:
        sys.exit("The engine that is running is the old one. Restart it first.")
    if all(now["settings"].get(k) == v for k, v in old.items()) and old != new:    # still exactly the old defaults: he never chose them
        call("/api/settings", new)
        done.append(f"risk setting moved to {config.DEFAULT_MODE}")
except OSError:
    sys.exit("The engine is not answering. Start it, then run this again.")

journal = Journal(config.STATE / "nightdesk.db")
check = journal.get("self_check")
if check and check.get("day") == "2026-10-13":
    journal.db.execute("DELETE FROM kv WHERE key='self_check'")
    journal.db.commit()
    journal.add("risk", "Correction to last night's self-check",
                "It said 'passed', but it had compared the live ranking with a price file that only held half of Tuesday's session, so it proved less than it claimed. "
                "The price file is fixed and the check will run properly after the next close.")
    done.append("stale self-check removed")
if not journal.get("last_speed"):
    journal.set("last_speed", {"day": "2026-10-13", "median_ms": 1800, "p90_ms": None, "samples": 3000, "decision_ms": 508.0, "fill_ms": 1535.0, "stop_us": None})
    done.append("first session's speed kept")
if not journal.get("upgraded_round2"):
    journal.set("upgraded_round2", True)
    mode = config.DEFAULT_MODE.capitalize()
    journal.add("decision", "Nightdesk was updated (14 October)",
                f"Risk setting is now {mode}: it holds less cash than before (change it in Settings). The forecaster is the same one, retrained on corrected prices. "
                "A price-data bug was fixed, it re-ranks more often, and the screens are new.")
    done.append("update note written")
print("; ".join(done) or "nothing to do")
