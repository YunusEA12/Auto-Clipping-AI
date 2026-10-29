import importlib.util
import json
import random
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "council", Path(__file__).resolve().parent.parent / "scripts" / "council.py")
council = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(council)


@pytest.fixture
def fake_reviewers(monkeypatch):
    monkeypatch.setitem(council.REVIEWERS, "gpt", lambda prompt: "gpt says")
    monkeypatch.setitem(council.REVIEWERS, "gemini", lambda prompt: "gemini says")
    monkeypatch.delenv("COUNCIL_BLOCKLIST", raising=False)


def test_job_is_anonymous_and_keeps_own_answer_local(tmp_path, fake_reviewers):
    job = council.run_job("review", "Frage", "Kontext", "meine Antwort", ["gpt", "gemini"],
                          jobs_dir=tmp_path, rng=random.Random(1))
    assert set(job["reviews"]) == {"A", "B"}
    assert {r["text"] for r in job["reviews"].values()} == {"gpt says", "gemini says"}
    stored = json.loads((tmp_path / f"{job['job_id']}.json").read_text(encoding="utf-8"))
    assert stored["own_answer"] == "meine Antwort"


def test_secret_in_context_blocks_before_any_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(council.REVIEWERS, "gpt", lambda p: calls.append(p) or "x")
    with pytest.raises(council.SensitiveContentError):
        council.run_job("answer", "Frage", "GEMINI_API_KEY=AIzaSyA1234567890abcdefghijklmnopqrstu",
                        "", ["gpt"], jobs_dir=tmp_path)
    assert calls == []


def test_blocklist_term_blocks(tmp_path, fake_reviewers, monkeypatch):
    monkeypatch.setenv("COUNCIL_BLOCKLIST", "Geheimkunde GmbH")
    with pytest.raises(council.SensitiveContentError):
        council.run_job("answer", "Analyse für geheimkunde gmbh", "", "", ["gpt"], jobs_dir=tmp_path)


def test_failed_reviewer_is_neutral_and_redacted(tmp_path, fake_reviewers, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")

    def boom(prompt):
        raise RuntimeError("HTTP 401 for key sk-test-value")

    monkeypatch.setitem(council.REVIEWERS, "gpt", boom)
    job = council.run_job("answer", "Frage", "", "", ["gpt", "gemini"], jobs_dir=tmp_path)
    failed = [r for r in job["reviews"].values() if not r["ok"]]
    assert failed[0]["text"] == "Gutachten nicht verfügbar."
    assert "sk-test-value" not in failed[0]["error"]


def test_first_rating_is_final(tmp_path, fake_reviewers):
    job = council.run_job("vote", "Frage", "", "C", ["gpt", "gemini"], jobs_dir=tmp_path)
    council.reveal(job["job_id"], "erste", 2, jobs_dir=tmp_path)
    again = council.reveal(job["job_id"], "zweite", 5, jobs_dir=tmp_path)
    assert again["rating"] == "erste" and again["contribution"] == 2
    assert "2 berechtigte neue Punkte" in council.status(jobs_dir=tmp_path)
