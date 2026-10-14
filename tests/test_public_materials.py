"""Public docs and media must never collect runtime account or computer facts."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explainer_uses_only_selected_simulations(tmp_path, monkeypatch):
    module = load_module("public_doc", ROOT / "tools/build_doc.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "research").mkdir()
    (tmp_path / "docs").mkdir()
    for name in ["modes.json", "grade_pit_rehearsal.json", "lab.json"]:
        (tmp_path / "research" / name).write_text(json.dumps({"historical": name}))
    (tmp_path / "docs/doc_template.html").write_text("const D=/*__ND_DATA__*/null;")
    (tmp_path / "data/state").mkdir(parents=True)
    (tmp_path / "data/state/session_report.json").write_text('{"private_account":"DO_NOT_PUBLISH"}')
    (tmp_path / "research/resources.json").write_text('{"private_machine":"DO_NOT_PUBLISH"}')
    page = module.build().read_text()
    assert "historical" in page
    assert "DO_NOT_PUBLISH" not in page


def test_voice_assets_require_correct_size_and_digest(tmp_path):
    module = load_module("voice_assets", ROOT / "video/download_models.py")
    sample = tmp_path / "voice.bin"
    sample.write_bytes(b"synthetic audio asset")
    import hashlib
    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    assert module.valid(sample, sample.stat().st_size, digest)
    assert not module.valid(sample, sample.stat().st_size + 1, digest)
    assert not module.valid(sample, sample.stat().st_size, "0" * 64)


def test_video_scene_data_reads_no_runtime_files(tmp_path, monkeypatch):
    module = load_module("public_video", ROOT / "video/src/make_data.py")
    monkeypatch.setattr(module, "BUILD", tmp_path)
    (tmp_path / "script.json").write_text('{"scenes":[]}')
    (tmp_path / "timeline.json").write_text('{"duration":1}')
    module.main()
    assert (tmp_path / "data.js").read_text().startswith("window.DATA = ")
