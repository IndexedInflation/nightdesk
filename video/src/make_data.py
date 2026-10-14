"""Build video scene data from the clean script and narration timeline only."""
import json
from pathlib import Path
BUILD = Path(__file__).resolve().parents[1] / "build"

def main():
    script = json.loads((BUILD / "script.json").read_text())
    timeline = json.loads((BUILD / "timeline.json").read_text())
    data = {"script": script, "timeline": timeline}
    (BUILD / "data.js").write_text("window.DATA = " + json.dumps(data).replace("</", "<\\/") + ";\n")
    print("Created scene data without account, logs or machine facts.")

if __name__ == "__main__":
    main()
