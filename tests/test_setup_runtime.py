"""First-run setup is offline, explicit, responsive and isolated from existing accounts."""
import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nightdesk import api, config, data, engine as E, setup_worker, universe
from nightdesk.config import Settings
from nightdesk.model import Ensemble
from nightdesk.news import Reader

HEADER = {"X-Nightdesk": "1"}


@pytest.fixture
def clean_engine(tmp_path, monkeypatch):
    state = tmp_path / "state"
    models = tmp_path / "models"
    cache = tmp_path / "cache"
    for directory in (state, models, cache):
        directory.mkdir()
    monkeypatch.setattr(config, "STATE", state)
    monkeypatch.setattr(E, "STATE", state)
    monkeypatch.setattr(E, "ROOT", tmp_path)
    monkeypatch.setattr(E, "MODELS", models)
    monkeypatch.setattr(data, "CACHE", cache)
    engine = E.Engine()
    engine.log = lambda *_: None
    return engine


@pytest.fixture
def web(clean_engine, monkeypatch):
    monkeypatch.setattr(api, "engine", clean_engine)
    with TestClient(api.app, base_url=f"http://127.0.0.1:{config.PORT}") as client:
        yield client


def test_first_launch_is_offline_and_never_searches_an_old_installation(clean_engine, monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("First launch tried to use the internet")
    monkeypatch.setattr(universe, "load_universe", forbidden)
    monkeypatch.setattr(data, "build_history", forbidden)
    asyncio.run(clean_engine.start())
    assert clean_engine.settings.network_enabled is False
    assert clean_engine.settings.auto_trade is False
    assert clean_engine.tasks == []
    assert clean_engine.news.reader.python is None and clean_engine.news.reader.model_dir is None
    snap = clean_engine.snapshot()
    assert snap["setup"]["phase"] == "waiting" and snap["engine"]["ready"] is False
    assert "setup" in snap["engine"]["message"].lower()


def test_clean_http_ui_and_local_research_work_without_history(web):
    assert web.get("/").status_code == 200
    assert web.get("/app.js").status_code == 200
    assert web.get("/api/health").json()["app"] == "nightdesk"
    assert web.get("/api/state").json()["account"]["positions"] == 0
    assert web.get("/api/research").status_code == 200
    assert web.get("/api/setup").json()["network_enabled"] is False
    assert web.get("/api/bars?symbol=SPY&span=day").status_code == 409


@pytest.mark.parametrize("body", [[], {"network_enabled": "yes"}, {"network_enabled": 1}, {"start": "false"}, {"other": True}])
def test_consent_requires_exact_booleans_and_known_fields(web, body):
    assert web.post("/api/setup", json=body, headers=HEADER).status_code == 400
    assert not web.get("/api/setup").json()["network_enabled"]


def test_setup_and_practice_trading_cannot_skip_consent(web):
    assert web.post("/api/setup", json={"start": True}, headers=HEADER).status_code == 409
    assert web.post("/api/setup", json={"network_enabled": True}).status_code == 403
    assert web.post("/api/settings", json={"network_enabled": True}, headers=HEADER).status_code == 400
    assert web.post("/api/settings", json={"auto_trade": True}, headers=HEADER).status_code == 409
    assert not web.post("/api/control", json={"action": "resume"}, headers=HEADER).json()["ok"]


def test_consent_is_saved_separately_from_start_and_does_not_enable_trading(web):
    state = web.post("/api/setup", json={"network_enabled": True}, headers=HEADER).json()
    assert state["network_enabled"] and not state["busy"]
    assert Settings.load().network_enabled and not Settings.load().auto_trade
    web.post("/api/setup", json={"network_enabled": False}, headers=HEADER)
    assert not Settings.load().network_enabled and not Settings.load().auto_trade


def test_configuration_is_validated_atomically_and_strings_are_not_booleans(web, clean_engine):
    before = clean_engine.settings.max_positions
    response = web.post("/api/settings", json={"max_positions": 20, "auto_trade": "false"}, headers=HEADER)
    assert response.status_code == 400 and clean_engine.settings.max_positions == before
    assert web.post("/api/settings", json={"max_positions": 20.5}, headers=HEADER).status_code == 400


def test_cross_site_websocket_cannot_read_an_account(web):
    with pytest.raises(WebSocketDisconnect):
        with web.websocket_connect(f"ws://127.0.0.1:{config.PORT}/ws", headers={"Origin": "https://other.example"}):
            pass
    with web.websocket_connect(f"ws://127.0.0.1:{config.PORT}/ws", headers={"Origin": f"http://127.0.0.1:{config.PORT}"}) as socket:
        assert socket.receive_json()["setup"]["network_enabled"] is False


def test_background_setup_reports_progress_and_can_retry_after_failure(clean_engine, monkeypatch):
    async def run():
        class FakeWorker:
            def __init__(self, code):
                self.returncode = None
                self.code = code
                self.stdout = asyncio.StreamReader()
                self.stdout.feed_data(b"Preparing public history\nTraining locally\n")
                self.stdout.feed_eof()
            async def wait(self):
                self.returncode = self.code
                return self.code
        calls = []
        async def spawn(*args, **kwargs):
            calls.append((args, kwargs))
            return FakeWorker(1 if len(calls) == 1 else 0)
        async def boot():
            clean_engine.ready = True
            clean_engine.setup_phase = "ready"
        monkeypatch.setattr(E.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(clean_engine, "_boot", boot)
        clean_engine.settings.network_enabled = True
        first = clean_engine.start_setup()
        assert first["busy"]
        clean_engine.start_setup()
        await clean_engine.setup_task
        assert len(calls) == 1 and clean_engine.setup_phase == "error"
        assert clean_engine.setup_process is None and not clean_engine.ready
        assert "Training locally" in clean_engine.setup_progress
        clean_engine.start_setup()
        await clean_engine.setup_task
        assert len(calls) == 2 and clean_engine.ready and not clean_engine.settings.auto_trade
        assert calls[0][0][1:] == ("-m", "nightdesk.setup_worker")
    asyncio.run(run())


def test_setup_boot_failure_stays_reviewable_and_does_not_leak_paths(clean_engine, monkeypatch):
    clean_engine.settings.network_enabled = True
    def broken():
        raise OSError("private user folder failed")
    monkeypatch.setattr(universe, "sector_map", broken)
    asyncio.run(clean_engine._boot())
    assert clean_engine.setup_phase == "error" and not clean_engine.ready
    assert "private user" not in json.dumps(clean_engine.setup_status())


def test_optional_reader_requires_explicit_local_files_and_exact_class_semantics(web, tmp_path):
    assert not Reader().available
    model = tmp_path / "reader"
    model.mkdir()
    (model / "model.safetensors").touch()
    (model / "config.json").write_text(json.dumps({"id2label": {"0": "positive", "1": "negative", "2": "neutral"}}))
    import sys
    payload = {"python": sys.executable, "model": str(model)}
    assert web.post("/api/reader", json=payload, headers=HEADER).status_code == 400
    (model / "config.json").write_text(json.dumps({"id2label": {"0": "good", "1": "bad", "2": "nothing"}}))
    assert web.post("/api/reader", json=payload, headers=HEADER).json()["available"]
    assert web.post("/api/reader", json={"python": "", "model": ""}, headers=HEADER).json()["available"] is False


def test_loader_rejects_a_legacy_binary_without_executing_it(tmp_path):
    model = tmp_path / "old.bin"
    model.write_bytes(b"\\x80\\x04legacy binary")
    with pytest.raises((ValueError, UnicodeDecodeError)):
        Ensemble.load(model)


def test_worker_uses_fixed_local_training_without_research_tuning(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(setup_worker, "MODELS", tmp_path)
    monkeypatch.setattr(universe, "symbols", lambda: ["SYNTH"])
    monkeypatch.setattr(data, "have_cache", lambda: False)
    monkeypatch.setattr(data, "build_history", lambda symbols, log: calls.append(("data", symbols)))
    monkeypatch.setattr(setup_worker.pit, "fit_production", lambda log: calls.append(("fit",)))
    assert setup_worker.main() == 0
    assert calls == [("data", ["SYNTH"]), ("fit",)]


def test_pause_and_disconnect_cancel_unfilled_practice_orders(clean_engine):
    clean_engine.broker.submit("SYNTH", "buy", 1, "test", now=time.time())
    clean_engine.control("pause")
    assert not clean_engine.broker.pending
    clean_engine.broker.submit("SYNTH", "buy", 1, "test", now=time.time())
    asyncio.run(clean_engine.disconnect())
    assert not clean_engine.broker.pending


@pytest.mark.parametrize("amount", [-1, 0, "not money", True])
def test_reset_rejects_invalid_cash_without_changing_the_account(web, amount):
    before = web.get("/api/state").json()["account"]["starting_cash"]
    response = web.post("/api/control", json={"action": "reset", "value": amount}, headers=HEADER)
    assert response.status_code == 400
    assert web.get("/api/state").json()["account"]["starting_cash"] == before


def test_stopping_cancels_background_jobs_and_waits_for_children(clean_engine):
    async def run():
        ended = asyncio.Event()
        class Child:
            returncode = None
            def terminate(self):
                self.returncode = -15
            async def wait(self):
                return self.returncode
        async def job():
            try:
                await asyncio.sleep(60)
            finally:
                ended.set()
        child = Child()
        clean_engine.retrain_process = child
        task = clean_engine._spawn_runtime(job())
        await asyncio.sleep(0)
        await clean_engine.stop()
        assert task.done() and ended.is_set() and child.returncode == -15
    asyncio.run(run())
