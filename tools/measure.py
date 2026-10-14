"""Measure this installation locally; never inventory unrelated apps or models."""
import json
import sys
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from nightdesk import config, sysinfo

def main():
    out = {"engine_mb": None, "model_file_mb": None}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{config.PORT}/api/health", timeout=3) as response:
            health = json.load(response)
        if health.get("app") == "nightdesk":
            out["engine_mb"] = sysinfo.footprint_mb(health["pid"])
    except (OSError, ValueError, KeyError):
        pass
    model = config.MODELS / "production.json"
    if model.exists():
        out["model_file_mb"] = round(model.stat().st_size / 1e6, 1)
    (config.LOGS / "local-resources.json").write_text(json.dumps(out))
    print(json.dumps(out))
if __name__ == "__main__":
    main()
