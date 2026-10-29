import json
import dashboard_api


def test_persistent_upload_summary_does_not_count_pending_as_published(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.json'
    monkeypatch.setattr(dashboard_api.upload_ledger, 'LEDGER_PATH', path)
    path.write_text(json.dumps({'a': {'tiktok': {'status': 'done'}},
                                'b': {'tiktok': {'status': 'pending'}},
                                'c': {'youtube': {'status': 'done'}}}))
    assert dashboard_api.tiktok_upload_summary() == {'done': 1, 'pending': 1, 'failed': 0}


def test_unavailable_upload_history_is_not_claimed_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_api.upload_ledger, 'LEDGER_PATH', tmp_path / 'missing.json')
    assert dashboard_api.tiktok_upload_summary()['done'] is None
