import transcribe


def test_multiple_streamers_do_not_each_claim_all_cores(monkeypatch):
    monkeypatch.setattr(transcribe.os, 'cpu_count', lambda: 12)
    monkeypatch.delenv('WHISPER_CPU_THREADS', raising=False)
    assert transcribe.whisper_cpu_threads() == 2


def test_cpu_threads_override_is_bounded_by_hardware(monkeypatch):
    monkeypatch.setattr(transcribe.os, 'cpu_count', lambda: 4)
    monkeypatch.setenv('WHISPER_CPU_THREADS', '16')
    assert transcribe.whisper_cpu_threads() == 4


def test_invalid_cpu_override_uses_safe_default(monkeypatch):
    monkeypatch.setattr(transcribe.os, 'cpu_count', lambda: 8)
    for value in ('0', '-1', 'wrong'):
        monkeypatch.setenv('WHISPER_CPU_THREADS', value)
        assert transcribe.whisper_cpu_threads() == 2
