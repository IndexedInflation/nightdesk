# Nightdesk

A local stock research app with **practice money only**. It ranks S&P 500 stocks, simulates a portfolio, and shows how historical experiments performed. It cannot connect to a broker or place real trades.

Each installation starts offline with a fresh practice account. You choose whether to download public market data, train a forecasting model on your own computer, and enable automatic practice trading. No account credentials or trained model weights are bundled.

## Install on a Mac

Requirements: macOS 14 or later, Python 3.11–3.13, internet for software installation and optional market data, several GB of free disk space, and enough memory for historical training. Training can use several GB of RAM. Apple Silicon is the primary native target; Intel Mac and Linux browser support are checked in CI.

1. Download this repository using **Code → Download ZIP**, then extract it into a writable folder.
2. Open Terminal in that folder and run `bash Install.command`. The installer checks Python, creates an isolated environment and installs the pinned app dependencies. It builds the optional Mac window if Apple command line tools are available.
3. Run `bash Launch.command`. On first launch, read the setup screen and choose **Download data and train my model**. After training completes, enable practice trading only when you choose.

You can also clone with `git clone https://github.com/IndexedInflation/nightdesk.git`.

If Terminal is unfamiliar: type `cd `, drag the extracted folder into Terminal, press Return, then paste the command above. After executability is preserved (for example by Git clone), you can double-click the command files.

The browser version works without a native app. To install without building the optional Mac window, run `bash scripts/install.sh --no-native`; launch using `bash Launch.command --browser`. Keep the generated `Nightdesk.app` beside the source folder; it is a local wrapper rather than a standalone distributable app.

Software installation downloads Python dependencies from PyPI. It does not download market history, train a model, or turn on practice trading.

## First launch and connections

The app displays the setup status, progress and any error. Downloading long price histories and training can take an hour or more. Yahoo can limit requests or return incomplete data; retry after the reported problem is resolved. Nightdesk blocks practice trading until its model and recent history are ready.

- **Market data:** public Yahoo Finance data through yfinance, plus Wikipedia index membership. No Yahoo login or API key is needed. Consent is stored in your local settings. Disconnect from the setup controls to return offline.
- **Forecaster:** trained locally from your downloaded data. The model uses a declarative JSON format. The app does not download or execute someone else's pickle model.
- **Optional headline reader:** disabled until you configure your own compatible model and Python environment in Settings. It is not needed for the core app. See [the model guide](docs/MODELS.md).
- **Optional explainer voice:** separate public Kokoro assets, downloaded only if you choose to build the video. See [video instructions](video/README.md).

yfinance is an unofficial research tool. Yahoo data has its own usage terms and is intended for personal use; the repository does not redistribute cached Yahoo data. See [yfinance's upstream notice](https://github.com/ranaroussi/yfinance).

## Your files and privacy

Your practice account, settings, downloaded prices, own research, models and logs stay in a user data folder:

| System | Default data folder |
|---|---|
| macOS | `~/Library/Application Support/Nightdesk` |
| Linux | `~/.local/share/nightdesk` (or under `XDG_DATA_HOME`) |
| Windows | `%LOCALAPPDATA%/Nightdesk` (experimental; not currently a supported installer target) |

Set `NIGHTDESK_HOME` to use a different writable folder. The server listens only on your computer at `http://127.0.0.1:8791`. Set `NIGHTDESK_PORT` for another port if needed.

There is no central Nightdesk account or telemetry service. Enabling market data contacts the public providers from your own connection. Runtime data is excluded from Git. Never upload your data directory, logs, credentials or model weights. Read [the publication privacy policy](docs/PRIVACY.md) before sharing changes.

## What the research says

The included results are **historical simulations**, not the performance of your newly trained model. In the fairer 2010–2024 simulation, the Steady setting returned about 12.4% per year compared with 13.8% for the S&P 500, with a smaller worst fall (28% compared with 34%). Later-period comparisons were inspected repeatedly, so they are not an untouched test.

Yahoo lacks history for many delisted stocks. Costs and fills are simplified. Live intraday timing and historical closing-price execution differ. Nothing guarantees profits, complete data or uninterrupted service. The app is for research and education, not financial advice.

See [research methodology](research/README.md) and the Results screen for the full record, including rejected variants.

## Included parts

| Folder | Purpose |
|---|---|
| `engine/nightdesk/` | Prices, features, forecasting, practice broker, shared portfolio rules, local API |
| `ui/` | Setup and app screens, plain HTML/CSS/JavaScript |
| `shell/` | Source and build scripts for the optional Mac window |
| `scripts/` | Installer, launcher and dependency checks |
| `tests/` | Offline regression tests using synthetic markets and isolated accounts |
| `research/` | Reviewed historical simulation summaries and rejected experiments |
| `tools/`, `docs/` | Research helpers, privacy gate and clean explainer builder |
| `video/` | Clean narration, diagrams, voice-download and video-generation source |

Open `How-Nightdesk-Works.html` for the plain-English guide. It is generated with `python tools/build_doc.py` using historical research only.

## Development

After installation:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
NIGHTDESK_HOME="$(mktemp -d)" .venv/bin/python -m pytest -q
python3 tools/privacy_check.py --history
```

The privacy command checks staged files and every reachable Git revision. It rejects private runtime artifacts, binary weights, local home paths and common credential formats. Human review is also required; a scanner cannot prove every sentence is non-confidential.

Optional browser tools: `npm ci` then `npx playwright install chromium`. The browser smoke test creates and removes its own disposable account; run it with `node tools/uitest.mjs`. See [contributor handoff](AGENTS.md).

License: MIT for project source. Third-party packages, data providers and optional voice assets retain their own licenses and terms.
