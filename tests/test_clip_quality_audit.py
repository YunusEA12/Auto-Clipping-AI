import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import clip_quality_audit as audit


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(audit, 'AUDIT_ROOT', tmp_path / 'quality_audit')
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    audit.init_campaign(limit=10, start=start.isoformat())
    return tmp_path


def _capture(root, number, content=None):
    path = root / f'clip_{number}.mp4'
    path.write_bytes(content if content is not None else f'video {number}'.encode())
    clip = {'title': f'Clip {number}', 'start_time': 30, 'end_time': 40}
    transcript = {'language': 'de', 'segments': [
        {'start': 0, 'end': 2, 'text': 'unrelated'},
        {'start': 27, 'end': 31, 'text': 'context'},
        {'start': 31, 'end': 38, 'text': 'actual content'},
        {'start': 100, 'end': 102, 'text': 'unrelated later'},
    ]}
    return audit.capture_rendered(clip, path, transcript, Path('source.ts'), 'streamer')


def _fake_verdict(row):
    return {'content_hash': row['content_hash'], 'title': row['title'],
            'verdict': {'summary': 'Geprüft',
                        'categories': {key: {'score': 7, 'evidence': 'Zeitpunkt 0:02', 'confidence': 'medium'}
                                       for key in audit.CATEGORIES},
                        'improvements': [], 'critical_issues': [], 'limitations': ['Test']}}


def test_capture_requires_explicit_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'AUDIT_ROOT', tmp_path / 'missing')
    assert not audit.capture_rendered({}, tmp_path / 'missing.mp4', None, Path('source.ts'))
    assert not audit.AUDIT_ROOT.exists()


def test_campaign_initialization_never_resets_existing_selection(campaign):
    _capture(campaign, 1)
    state = audit.init_campaign(limit=20)
    assert state['limit'] == 10
    assert len(state['clips']) == 1


def test_capture_is_bounded_under_concurrent_streamers(campaign):
    with ThreadPoolExecutor(max_workers=8) as pool:
        selected = list(pool.map(lambda number: _capture(campaign, number), range(25)))
    state = audit._load()
    assert sum(selected) == 10
    assert len(state['clips']) == 10
    assert len({row['content_hash'] for row in state['clips']}) == 10
    assert all(Path(row['snapshot_path']).is_file() for row in state['clips'])


def test_content_hash_deduplicates_renamed_video(campaign):
    assert _capture(campaign, 1, b'identical bytes')
    assert not _capture(campaign, 2, b'identical bytes')
    assert len(audit._load()['clips']) == 1


def test_old_render_is_not_selected(campaign):
    path = campaign / 'old.mp4'
    path.write_bytes(b'old')
    old = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    os.utime(path, (old, old))
    assert not audit.capture_rendered({'title': 'old', 'start_time': 0, 'end_time': 5},
                                      path, None, Path('source.ts'))
    assert audit._load()['clips'] == []


def test_snapshot_and_relevant_transcript_survive_original_deletion(campaign):
    _capture(campaign, 1)
    row = audit._load()['clips'][0]
    snapshot = Path(row['snapshot_path'])
    (campaign / 'clip_1.mp4').unlink()
    assert snapshot.read_bytes() == b'video 1'
    transcript = json.loads((snapshot.parent / 'transcript.json').read_text())
    assert [segment['text'] for segment in transcript['segments']] == ['context', 'actual content']
    metadata = json.loads((snapshot.parent / 'metadata.json').read_text())
    assert metadata['source_video'] == 'source.ts'


def test_worker_persists_review_without_touching_original(campaign, monkeypatch):
    _capture(campaign, 1)
    monkeypatch.setattr(audit, 'evaluate_snapshot', _fake_verdict)
    assert audit.process_next()
    assert not audit.process_next()
    row = audit._load()['clips'][0]
    assert row['status'] == 'completed'
    assert (campaign / 'clip_1.mp4').exists()
    assert (Path(row['snapshot_path']).parent / 'assessment.json').exists()
    assert 'schnitt' in (audit.AUDIT_ROOT / 'report.md').read_text() or 'Bewertung' in (audit.AUDIT_ROOT / 'report.md').read_text()


def test_worker_failure_is_visible_and_retry_is_bounded(campaign, monkeypatch):
    _capture(campaign, 1)
    def fail(row):
        raise RuntimeError('decoder unavailable')
    monkeypatch.setattr(audit, 'evaluate_snapshot', fail)
    for expected in range(1, audit.MAX_ATTEMPTS + 1):
        assert audit.process_next()
        state = audit._load()
        assert state['clips'][0]['attempts'] == expected
        state['clips'][0].pop('retry_after', None)
        audit._save(state)
    assert audit._load()['clips'][0]['status'] == 'failed'
    assert not audit.process_next()
    assert (campaign / 'clip_1.mp4').exists()


def test_multiple_workers_claim_a_clip_once(campaign, monkeypatch):
    _capture(campaign, 1)
    calls = []
    def evaluate(row):
        calls.append(row['content_hash'])
        return _fake_verdict(row)
    monkeypatch.setattr(audit, 'evaluate_snapshot', evaluate)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: audit.process_next(), range(4)))
    assert len(calls) == 1
    assert audit._load()['clips'][0]['status'] == 'completed'


def test_crashed_claim_is_recovered(campaign, monkeypatch):
    _capture(campaign, 1)
    state = audit._load()
    state['clips'][0].update(status='processing', attempts=1,
                            claimed_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    audit._save(state)
    monkeypatch.setattr(audit, 'evaluate_snapshot', _fake_verdict)
    assert audit.process_next()
    assert audit._load()['clips'][0]['attempts'] == 2


def test_checkpoint_uses_only_real_unique_metrics_and_no_model(campaign, monkeypatch):
    _capture(campaign, 1)
    row = audit._load()['clips'][0]
    (campaign / 'viral_memory.json').write_text(json.dumps({
        'record': {'title': row['title'], 'checked_at': '2026-10-28T00:00:00Z',
                   'uploaded_at': datetime.now(timezone.utc).isoformat(),
                   'tiktok_views': 14, 'tiktok_likes': 0, 'viral_score': 10}
    }))
    monkeypatch.setattr(audit, 'evaluate_snapshot', lambda row: pytest.fail('No model call allowed'))
    report = audit.write_checkpoint()
    performance = report['clips'][0]['performance']
    assert performance['status'] == 'measured'
    assert performance['tiktok_views'] == 14
    assert performance['tiktok_likes'] == 0
    assert 'viral_score' not in performance
    assert 'retention' not in performance


def test_duplicate_titles_are_not_fabricated_metric_matches(campaign):
    row = {'title': 'same', 'content_hash': 'abc'}
    memory = {'first': {'title': 'same', 'checked_at': 'now', 'views': 10},
              'second': {'title': 'same', 'checked_at': 'now', 'views': 20}}
    assert audit._measured_metrics(row, memory)['status'] == 'unavailable'


def test_old_same_title_is_not_matched_to_new_render(campaign):
    row = {'title': 'same', 'content_hash': 'abc',
           'rendered_at': datetime.now(timezone.utc).isoformat()}
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    memory = {'old': {'title': 'same', 'checked_at': old, 'uploaded_at': old, 'views': 100}}
    assert audit._measured_metrics(row, memory)['status'] == 'unavailable'


def test_due_checkpoint_is_preserved(campaign, monkeypatch):
    state = audit._load()
    state['check_at'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    audit._save(state)
    audit.write_checkpoint()
    first = (audit.AUDIT_ROOT / 'checkpoint_12h.json').read_bytes()
    _capture(campaign, 1)
    audit.write_checkpoint()
    assert (audit.AUDIT_ROOT / 'checkpoint_12h.json').read_bytes() == first
    assert json.loads((audit.AUDIT_ROOT / 'report.json').read_text())['captured'] == 1


def test_timezone_required(campaign):
    with pytest.raises(ValueError, match='timezone'):
        audit._date('2026-10-28T12:00:00')


def test_unavailable_score_is_schema_valid():
    verdict = audit.CategoryVerdict(score=None, confidence='unavailable', evidence='Ton fehlt')
    assert verdict.score is None
    with pytest.raises(ValueError):
        audit.CategoryVerdict(score=11, confidence='high', evidence='invalid')


def _stub_model(monkeypatch, row, *, file_upload=False):
    from google import genai
    calls = {'deleted': [], 'contents': None, 'closed': False, 'file_get': 0}
    def generate_content(**kwargs):
        calls['contents'] = kwargs['contents']
        return SimpleNamespace(parsed=_fake_verdict(row)['verdict'])
    def upload(**kwargs):
        return SimpleNamespace(name='files/test', state=SimpleNamespace(name='PROCESSING'))
    def get(**kwargs):
        calls['file_get'] += 1
        return SimpleNamespace(name='files/test', uri='https://example.invalid/video',
                               state=SimpleNamespace(name='ACTIVE'))
    fake = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content),
        files=SimpleNamespace(upload=upload, get=get, delete=lambda **kwargs: calls['deleted'].append(kwargs)),
        close=lambda: calls.update(closed=True),
    )
    monkeypatch.setattr(genai, 'Client', lambda **kwargs: fake)
    monkeypatch.setattr(audit, 'measure_video', lambda path: {'has_audio': False, 'decode_ok': True})
    monkeypatch.setattr(audit.llm_utils, 'call_with_fallback', lambda callback, **kwargs: callback('test-model'))
    monkeypatch.setattr(audit.time, 'sleep', lambda seconds: None)
    return calls


def test_model_receives_full_inline_video_and_missing_audio_is_not_passed(campaign, monkeypatch):
    _capture(campaign, 1)
    row = audit._load()['clips'][0]
    calls = _stub_model(monkeypatch, row)
    result = audit.evaluate_snapshot(row)
    assert calls['contents'][0].inline_data.mime_type == 'video/mp4'
    assert calls['contents'][0].inline_data.data == b'video 1'
    assert result['verdict']['categories']['audio']['score'] is None
    assert result['verdict']['categories']['audio']['confidence'] == 'unavailable'
    assert calls['closed']


def test_large_video_waits_for_file_processing_and_cleans_up(campaign, monkeypatch):
    _capture(campaign, 1)
    row = audit._load()['clips'][0]
    with Path(row['snapshot_path']).open('r+b') as file:
        file.truncate(16 * 1024 * 1024)
    calls = _stub_model(monkeypatch, row)
    audit.evaluate_snapshot(row)
    assert calls['file_get'] == 1
    assert calls['contents'][0].file_data.file_uri == 'https://example.invalid/video'
    assert calls['deleted'] == [{'name': 'files/test'}]
    assert calls['closed']
