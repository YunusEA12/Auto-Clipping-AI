import json

from autoclip.configuration import streamers
from autoclip.control import process_supervisor
from autoclip.control import upload_worker as worker
from autoclip.publishing import upload_ledger, upload_manager


def test_paused_fleet_does_not_scan_or_publish(monkeypatch):
    monkeypatch.setattr(process_supervisor, "read_fleet_target_state", lambda: "paused")
    monkeypatch.setattr(worker, "candidates", lambda: (_ for _ in ()).throw(AssertionError("no scan")))
    assert worker.run_once(publish=True) == {"status": "paused"}


def test_pacing_defers_before_hashing_backlog(monkeypatch):
    monkeypatch.setattr(process_supervisor, "read_fleet_target_state", lambda: "running")
    monkeypatch.setattr(upload_manager, "tiktok_pacing_status", lambda: {"ready": False, "reason": "upload_limits"})
    monkeypatch.setattr(worker, "candidates", lambda: (_ for _ in ()).throw(AssertionError("no scan")))
    assert worker.run_once(publish=True)["status"] == "waiting"


def test_missing_publish_flag_never_calls_deployment(monkeypatch):
    monkeypatch.setattr(process_supervisor, "read_fleet_target_state", lambda: "running")
    monkeypatch.setattr(worker, "candidates", lambda: [object()])
    monkeypatch.setattr(
        worker.auto_pilot, "run_deployment_phase", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no upload"))
    )
    assert worker.run_once() == {"status": "dry_run", "eligible": 1, "publish": False}


def test_worker_only_selects_enabled_assessed_technical_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        streamers,
        "load_streamers",
        lambda: [
            {"name": "enabled", "auto_upload": True, "publish": True},
            {"name": "disabled", "auto_upload": True, "publish": False},
        ],
    )
    for owner, name, score, qc in [
        ("enabled", "good", 8, True),
        ("enabled", "bad", -8, True),
        ("enabled", "qc", 8, False),
        ("disabled", "hidden", 10, True),
    ]:
        p = tmp_path / owner / f"{name}.mp4"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(name.encode())
        p.with_suffix(".json").write_text(json.dumps({"title": name, "reward_score": score, "auto_qc": {"passed": qc}}))
    assert [item[0]["title"] for item in worker.candidates(tmp_path)] == ["good"]
    upload_ledger.mark_done(upload_ledger.compute_content_hash(tmp_path / "enabled/good.mp4"), "tiktok")
    assert worker.candidates(tmp_path) == []


def test_worker_can_publish_without_any_live_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(process_supervisor, "read_fleet_target_state", lambda: "running")
    monkeypatch.setattr(upload_manager, "tiktok_pacing_status", lambda: {"ready": True})
    video = tmp_path / "clip.mp4"
    monkeypatch.setattr(worker, "candidates", lambda: [({"title": "approved"}, video, 8, "creator")])
    calls = []
    monkeypatch.setattr(worker.auto_pilot, "run_deployment_phase", lambda *a, **k: calls.append((a, k)) or (1, 0))
    assert worker.run_once(publish=True)["uploaded"] == 1
    assert len(calls) == 1
    assert calls[0][1] == {"publish": True, "streamer_name": "creator"}
