import json
import random

import analyze
import chat_signal


def realistic_log(seed=7):
    """10 min of chat: ~1 msg / 3 s baseline, a burst of ~40 msgs around t=312-320s."""
    rnd = random.Random(seed)
    stamps = sorted(rnd.uniform(0, 600) for _ in range(200))
    stamps += [312 + rnd.uniform(0, 8) for _ in range(40)]
    return sorted(stamps)


def test_burst_is_detected_and_baseline_is_not():
    spikes = chat_signal.find_chat_spikes(realistic_log())
    assert spikes
    assert any(305 <= s["timestamp"] <= 320 for s in spikes)
    assert all(s["messages"] >= chat_signal.CHAT_MIN_MESSAGES for s in spikes)
    assert len(spikes) <= 3


def test_uniform_or_empty_chat_has_no_spikes():
    assert chat_signal.find_chat_spikes([]) == []
    assert chat_signal.find_chat_spikes([float(i) for i in range(0, 300, 2)]) == []
    assert chat_signal.find_chat_spikes([1.0, 2.0]) == []


def test_quiet_chat_small_blip_is_ignored():
    # 3 messages in one window of an otherwise nearly silent chat: below CHAT_MIN_MESSAGES.
    stamps = [10.0, 80.0, 150.0, 200.1, 200.5, 201.0, 260.0]
    assert chat_signal.find_chat_spikes(stamps) == []


def test_adjacent_spike_windows_merge_keeping_peak():
    stamps = [float(t) for t in range(0, 300, 10)]
    stamps += [100 + i * 0.2 for i in range(20)]   # window 100-105: 20
    stamps += [105 + i * 0.5 for i in range(10)]   # window 105-110: 10
    spikes = chat_signal.find_chat_spikes(stamps)
    assert len(spikes) == 1
    assert spikes[0]["timestamp"] == 100.0


def test_parse_formats(tmp_path):
    jl = tmp_path / "a.jsonl"
    jl.write_text('{"t": 3.5, "user": "x"}\n{"timestamp": 1}\nbroken\n', encoding="utf-8")
    assert chat_signal.parse_chat_log(jl) == [1.0, 3.5]

    arr = tmp_path / "b.json"
    arr.write_text(json.dumps([5, {"offset": 2.5}, "x"]), encoding="utf-8")
    assert chat_signal.parse_chat_log(arr) == [2.5, 5.0]

    txt = tmp_path / "c.log"
    txt.write_text("[00:01:05] viber: LOL\n[02:10] noob: gg\n42.5 someone: hi\n\nzzz\n", encoding="utf-8")
    assert chat_signal.parse_chat_log(txt) == [42.5, 65.0, 130.0]

    assert chat_signal.parse_chat_log(tmp_path / "missing.log") == []


def test_prompt_section_format_matches_energy_section():
    assert chat_signal.build_chat_prompt_section([]) == ""
    section = chat_signal.build_chat_prompt_section([{"timestamp": 312.0, "messages": 31}])
    assert "[05:12.0] CHAT SPIKE (31 messages" in section
    assert analyze.format_timestamp(312.0) == "05:12.0"
    assert "BEFORE" in section


def test_select_clips_prompt_includes_chat_section(monkeypatch):
    captured = {}

    class FakeClient:
        class models:
            @staticmethod
            def generate_content(model, contents, config):
                captured["contents"] = contents
                raise RuntimeError("stop here")

    monkeypatch.setattr(analyze.genai, "Client", lambda: FakeClient)
    monkeypatch.setattr(analyze.llm_utils, "call_with_fallback", lambda fn, *a, **k: fn("m"))
    try:
        analyze.select_clips("hello", chat_spikes=[{"timestamp": 60.0, "messages": 20}])
    except Exception:
        pass
    assert "CHAT SPIKE" in captured["contents"][0]
