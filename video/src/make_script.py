"""Create a shareable video narration; no local account or machine details."""
import json
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]

def main():
    scenes = [
        ("what", "What Nightdesk does", [
            "Nightdesk is a *stock research app* that trades with pretend money.",
            "It runs on your computer. It never connects to a broker or moves real money.",
            "A new installation starts with a *fresh practice account*."]),
        ("setup", "Your first launch", [
            "The app starts offline. You choose whether to enable public market data.",
            "When you agree, it downloads stock history from Yahoo Finance and index membership from Wikipedia.",
            "Your computer trains its own forecasting model. Downloads and training can take a long time.",
            "The app shows progress. If a provider fails or limits requests, you can retry.",
            "Automatic practice trading stays off until you turn it on."]),
        ("day", "A trading day", [
            "Prices arrive while the New York market is open.",
            "Nightdesk measures *price and volume patterns* and averages a committee of small models.",
            "It ranks stocks. Portfolio rules choose holdings, limit exposure and manage simulated risk.",
            "Practice orders fill at the next fresh price, with simulated costs."]),
        ("tests", "What the tests mean", [
            "Historical tests are included for inspection. They are *simulations*, not promises.",
            "The test trains on earlier years and scores later years. It includes trading costs.",
            "Some delisted companies have no usable prices, so *survivorship bias remains*.",
            "Several ideas looked strong on development years and failed later periods. Those candidates were rejected.",
            "Simplified fills, delayed data and repeated use of held-back periods limit what the numbers can tell us."]),
        ("privacy", "What stays on your computer", [
            "Your account, settings, downloaded prices and trained model stay in your local data folder.",
            "There is no central Nightdesk server, Yahoo login, API key or broker credential.",
            "Optional headline classification requires a model *you configure yourself*. It starts disabled.",
            "The shared repository includes source and historical research. It excludes private logs, accounts and model weights."]),
        ("finish", "Start with practice", [
            "Launch the app, read the setup screen, and choose whether to download data.",
            "Let the model finish training before enabling practice trading.",
            "Review the results and risk settings. More exposure can mean larger losses.",
            "Nightdesk is for *research and education*. It is not financial advice."])
    ]
    out = {"voice": "af_heart", "speed": 0.95, "fps": 24, "scenes": [], "quizzes": []}
    for chapter, (sid, title, lines) in enumerate(scenes, 1):
        beats = [{"id": f"{sid}.{i}", "say": line, "tts": line.replace("*", ""), "gap": 0.5}
                 for i, line in enumerate(lines, 1)]
        out["scenes"].append({"id": sid, "title": title, "chapter": chapter, "beats": beats})
    (HERE / "build").mkdir(exist_ok=True)
    (HERE / "build/script.json").write_text(json.dumps(out, indent=2))
    print(f"Created {len(scenes)} chapters with shareable narration.")

if __name__ == "__main__":
    main()
