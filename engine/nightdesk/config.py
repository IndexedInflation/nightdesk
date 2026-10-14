"""Paths and settings shared by every part of the Nightdesk engine.

Nothing here talks to the internet or moves money. It only says where files live
and holds the numbers a person may want to change (starting cash, risk limits).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
# Source checkouts and built wheels have independent read-only app assets.
ASSET_ROOT = SOURCE_ROOT if (SOURCE_ROOT / "ui" / "index.html").exists() else Path(__file__).with_name("assets")


def default_home() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Nightdesk"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Nightdesk"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "nightdesk"


ROOT = Path(os.environ.get("NIGHTDESK_HOME", default_home())).expanduser().resolve()
DATA = ROOT / "data"
CACHE = DATA / "cache"
MODELS = DATA / "models"
STATE = DATA / "state"
RESEARCH = ROOT / "research"           # this user's own research output
PUBLIC_RESEARCH = ASSET_ROOT / "research"  # bundled historical simulations
LOGS = ROOT / "logs"
UI_DIR = ASSET_ROOT / "ui"

for _p in (CACHE, MODELS, STATE, RESEARCH, LOGS):
    _p.mkdir(parents=True, exist_ok=True)

HOST = "127.0.0.1"                    # only this computer can reach the engine
PORT = int(os.environ.get("NIGHTDESK_PORT", "8791"))

HISTORY_START = "2005-01-01"
BENCHMARK = "SPY"
SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"]
EXTRA_SYMBOLS = [BENCHMARK, "QQQ", "IWM", "^VIX"] + SECTOR_ETFS

# The portfolio recipe chosen from the 9-way comparison on 2010-2024 (see research/variants.json).
MAX_POSITIONS = 50         # how many stocks to hold (chosen on the fairer test, see research/pit_menu.json)
HOLD_BUFFER = 4.0          # keep a stock while it ranks in the top (MAX_POSITIONS x 4)
SCORE_SMOOTHING = 10       # blend each day's score with the previous days' (10-day span)

# What the forecaster is taught to predict ("the answer sheet").
#   "plain" = which stocks went up most over the next week and month. LIVE. Held up on the months kept back for the exam.
#   "risk"  = the same, divided by how jumpy each stock has been (prefers steady climbers). Looked far better on
#             2010-2024, then made 5% a year against the market's 19% in the exam months. NOT live (research/candidate_risk/).
#   "blend" = both sheets, averaged. Also failed the exam months. NOT live (research/lab/blend_check.json).
TARGET = "plain"
EVENT_FAMILIES: tuple = () # extra signal families fed to the live forecaster: "earn" and/or "analyst" (see events.py)

# How much risk to take: three tested settings. Test and live read the same numbers.
#   target_vol   = hold cash whenever the portfolio's recent swings run above this yearly rate
#   trend_filter = halve the bet while the S&P 500 is below its 200-day average
#   max_drawdown_limit = the live safety switch; kept well beyond the worst fall that mode had in testing
RISK_MODES = {
    "cautious": {"target_vol": 0.15, "trend_filter": True, "max_drawdown_limit": 0.25},
    "steady": {"target_vol": 0.25, "trend_filter": False, "max_drawdown_limit": 0.40},
    "full": {"target_vol": 1.0, "trend_filter": False, "max_drawdown_limit": 0.60},
}
DEFAULT_MODE = "steady"    # same gain for the risk taken as Cautious on 2010-2024, with more of the market's return (research/modes.json)
_MODE = RISK_MODES[DEFAULT_MODE]

# Optional reader paths are supplied by each user; there is no inherited model.
READER_PYTHON = os.environ.get("NIGHTDESK_READER_PYTHON", "")
READER_MODEL = os.environ.get("NIGHTDESK_READER_MODEL", "")


@dataclass
class Settings:
    """Everything a person can change from the app's Settings screen."""

    starting_cash: float = 100_000.0     # pretend dollars
    auto_trade: bool = False              # let the engine place paper orders by itself
    max_positions: int = MAX_POSITIONS   # how many stocks to hold at once
    hold_buffer: float = HOLD_BUFFER     # keep a stock until it drops out of the top (max_positions x this)
    score_smoothing: int = SCORE_SMOOTHING  # days of scores blended together (steadier ranks, fewer trades)
    max_weight: float = 0.08             # no single stock above 8% of the account
    max_sector_weight: float = 0.30      # no single industry above 30%
    target_vol: float = _MODE["target_vol"]        # hold cash when recent swings run above this yearly rate (see RISK_MODES)
    trend_filter: bool = _MODE["trend_filter"]     # halve exposure when the market is below its 200-day average
    slippage_bps: float = 3.0            # assumed cost per trade, in hundredths of a percent
    daily_loss_limit: float = 0.03       # stop trading for the day after losing 3%
    max_drawdown_limit: float = _MODE["max_drawdown_limit"]   # sell everything after falling this far from the peak (set per risk mode)
    position_stop: float = 0.12          # sell a stock that falls 12% below what we paid
    news_veto: bool = False              # let the headline reader block a buy after fresh bad news (off: unproven, on trial)
    rebalance_minutes_before_close: int = 15
    min_trade_dollars: float = 250.0     # ignore tiny adjustments (they only cost money)
    rebalance_band: float = 0.25         # leave a position alone unless it is 25% off its target size
    keep_awake: bool = False              # stop the Mac sleeping while the engine runs
    network_enabled: bool = False       # explicit consent to contact public data sources
    reader_python: str = READER_PYTHON
    reader_model: str = READER_MODEL
    reader_label_order: tuple[str, ...] = ("good", "bad", "nothing")
    extras: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        path = STATE / "settings.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
                return cls(**known)
            except (ValueError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        tmp = STATE / "settings.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(STATE / "settings.json")

    def to_dict(self) -> dict:
        return asdict(self)
