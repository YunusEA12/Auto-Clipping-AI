"""Keep pipeline placeholder videos out of a running quality-audit campaign."""

import pytest
import clip_quality_audit


@pytest.fixture(autouse=True)
def _isolated_quality_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(clip_quality_audit, 'AUDIT_ROOT', tmp_path / 'quality_audit')
