"""Top up the price history by hand, with patience (Yahoo rate-limits bursts).

    .venv/bin/python tools/refresh_history.py            (tries up to 8 times, 2 minutes apart)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from nightdesk import data, research, universe          # noqa: E402

for attempt in range(8):
    try:
        data.update_history(universe.symbols(), log=lambda m: print(m, flush=True))
        panel = research.load_research_panel()
        last = panel["close"].iloc[-1]
        print(f"history now ends {panel['close'].index[-1].date()} with {int(last.notna().sum())} of {len(last)} stocks priced", flush=True)
        break
    except data.IncompleteHistory as exc:
        print(f"try {attempt + 1}: {exc}; waiting 2 minutes", flush=True)
        time.sleep(120)
