"""Build the public explainer using historical simulation summaries only.
Never reads the running account, logs, model weights, or machine inventory.
Run: python tools/build_doc.py
"""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def build() -> Path:
    names = {"modes": "modes.json", "grade": "grade_pit_rehearsal.json", "lab": "lab.json"}
    data = {key: json.loads((ROOT / "research" / name).read_text()) for key, name in names.items()}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = (ROOT / "docs/doc_template.html").read_text().replace("/*__ND_DATA__*/null", blob)
    out = ROOT / "How-Nightdesk-Works.html"
    out.write_text(page)
    return out

if __name__ == "__main__":
    print(build())
