import json
import dashboard_api


def test_quality_report_is_available_after_transfer(tmp_path):
    report = {'clips': [], 'completed': 10, 'target': 10}
    (tmp_path / 'report.json').write_text(json.dumps(report))
    assert dashboard_api.load_quality_audit_report(tmp_path) == report


def test_missing_or_invalid_report_is_not_presented_as_completed(tmp_path):
    assert dashboard_api.load_quality_audit_report(tmp_path) is None
    (tmp_path / 'report.json').write_text('[]')
    assert dashboard_api.load_quality_audit_report(tmp_path) is None
