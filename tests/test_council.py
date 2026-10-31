import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "council", Path(__file__).resolve().parent.parent / "scripts" / "council.py")
council = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(council)


@pytest.fixture(autouse=True)
def no_env_blocklist(monkeypatch):
    monkeypatch.delenv("COUNCIL_BLOCKLIST", raising=False)


def test_job_stores_review_own_answer_and_chatgpt_prompt(tmp_path):
    job = council.run_job("review", "Frage", "Kontext", "meine Antwort",
                          jobs_dir=tmp_path, gemini=lambda p: "gemini says")
    assert job["gemini"] == {"ok": True, "text": "gemini says"}
    stored = json.loads((tmp_path / f"{job['job_id']}.json").read_text(encoding="utf-8"))
    assert stored["own_answer"] == "meine Antwort"
    prompt = Path(job["chatgpt_prompt"]).read_text(encoding="utf-8")
    assert "Frage" in prompt and "Kontext" in prompt and "meine Antwort" not in prompt


def test_secret_blocks_before_gemini_and_prompt_file(tmp_path):
    calls = []
    with pytest.raises(council.SensitiveContentError):
        council.run_job("answer", "Frage", "GEMINI_API_KEY=AIzaSyA1234567890abcdefghijklmnopqrstu", "",
                        jobs_dir=tmp_path, gemini=lambda p: calls.append(p) or "x")
    assert calls == []
    assert not list(tmp_path.glob("*"))


def test_blocklist_term_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_BLOCKLIST", "Geheimkunde GmbH")
    with pytest.raises(council.SensitiveContentError):
        council.run_job("answer", "Analyse für geheimkunde gmbh", "", "",
                        jobs_dir=tmp_path, gemini=lambda p: "x")


def test_gemini_failure_is_reported_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-value")

    def boom(prompt):
        raise RuntimeError("HTTP 429 for key AIza-test-value")

    job = council.run_job("answer", "Frage", "", "", jobs_dir=tmp_path, gemini=boom)
    assert not job["gemini"]["ok"]
    assert "AIza-test-value" not in job["gemini"]["error"]
    assert "429" in council.status(jobs_dir=tmp_path)


def test_first_rating_is_final(tmp_path):
    job = council.run_job("vote", "Frage", "", "C", jobs_dir=tmp_path, gemini=lambda p: "x")
    council.rate(job["job_id"], "erste", 2, jobs_dir=tmp_path)
    again = council.rate(job["job_id"], "zweite", 5, jobs_dir=tmp_path)
    assert again["rating"] == "erste" and again["contribution"] == 2
    assert "2 berechtigte neue Punkte" in council.status(jobs_dir=tmp_path)


def test_no_paid_api_is_referenced():
    source = Path(council.__file__).read_text(encoding="utf-8")
    assert "api.openai.com" not in source and "OPENAI_API_KEY" not in source
