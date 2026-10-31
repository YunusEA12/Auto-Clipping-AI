import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import subprocess

from filelock import FileLock
import pytest

import atomic_io
import project_guardian as guardian

NOW = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)


def setup_root(tmp_path, monkeypatch, paused=False):
    monkeypatch.setattr(guardian.psutil, 'disk_usage', lambda _: SimpleNamespace(free=10 * 1024 ** 3, percent=50))
    atomic_io.atomic_write_json(tmp_path / 'fleet_control.json', {'target_state': 'paused' if paused else 'running'})
    atomic_io.atomic_write_json(tmp_path / 'orchestrator_state.json',
                               {'status': 'running', 'last_check': NOW.isoformat(), 'poll_interval': 90})
    return tmp_path


def test_paused_fleet_does_not_report_stale_or_call_agent(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch, paused=True)
    (tmp_path / 'orchestrator_state.json').unlink()
    monkeypatch.setattr(guardian, 'request_advice', lambda *a, **kw: pytest.fail('Paused fleet should remain quiet'))
    result = guardian.run_once(tmp_path, enable_agent=True, now=NOW)
    assert result['status'] == 'healthy'
    assert result['agent']['status'] == 'not_needed'


@pytest.mark.parametrize('agent_status', ['advice_received', 'agent_unavailable'])
def test_unchanged_issue_retries_hourly_not_every_poll(tmp_path, monkeypatch, agent_status):
    setup_root(tmp_path, monkeypatch)
    (tmp_path / 'orchestrator_state.json').unlink()
    calls = []
    monkeypatch.setattr(guardian, 'request_advice', lambda *a, **kw: calls.append(1) or {'status': agent_status})
    assert guardian.run_once(tmp_path, enable_agent=True, now=NOW)['agent']['status'] == agent_status
    assert guardian.run_once(tmp_path, enable_agent=True, now=NOW + timedelta(minutes=10))['agent']['status'] == 'not_needed'
    assert calls == [1]
    assert guardian.run_once(tmp_path, enable_agent=True, now=NOW + timedelta(hours=1))['agent']['status'] == agent_status
    assert calls == [1, 1]


def test_agent_disabled_never_launches(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    (tmp_path / 'orchestrator_state.json').unlink()
    monkeypatch.setattr(guardian.subprocess, 'run', lambda *a, **kw: pytest.fail('no subprocess'))
    assert guardian.run_once(tmp_path, now=NOW)['agent']['status'] == 'disabled'


def test_daily_run_ceiling(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    (tmp_path / 'orchestrator_state.json').unlink()
    atomic_io.atomic_write_json(tmp_path / 'guardian_state.json', {'day': NOW.date().isoformat(), 'agent_runs': 8})
    monkeypatch.setattr(guardian, 'request_advice', lambda *a, **kw: pytest.fail('budget exhausted'))
    assert guardian.run_once(tmp_path, now=NOW, enable_agent=True)['agent']['status'] == 'daily_limit_reached'


def test_timeout_reserves_quota_and_never_echoes_diagnostics(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    (tmp_path / 'orchestrator_state.json').unlink()
    def fail(*args, **kw):
        raise subprocess.TimeoutExpired('secret-command', 600)
    monkeypatch.setattr(guardian, 'request_advice', fail)
    result = guardian.run_once(tmp_path, now=NOW, enable_agent=True)
    assert result['agent'] == {'status': 'agent_unavailable', 'error_type': 'TimeoutExpired'}
    assert json.loads((tmp_path / 'guardian_state.json').read_text())['agent_runs'] == 1
    assert 'secret-command' not in json.dumps(result)


def test_environment_does_not_forward_api_or_cloud_credentials(monkeypatch):
    for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS',
                'AGY_ADC_AUTH', 'GOOGLE_GENAI_USE_VERTEXAI', 'GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY'):
        monkeypatch.setenv(key, 'secret')
    monkeypatch.setenv('HOME', 'cached-google-profile')
    env = guardian.subscription_environment()
    assert env['HOME'] == 'cached-google-profile'
    assert 'secret' not in env.values()


def test_cli_uses_print_json_timeout_and_isolated_workspace(tmp_path, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"result":"fine"}')
    monkeypatch.setattr(guardian.subprocess, 'run', run)
    result = guardian.request_advice({'issues': []}, tmp_path / 'isolated')
    args, kwargs = calls[0]
    assert args[0:2] == ['agy', '-p']
    assert args[-2:] == ['--output-format', 'json']
    assert args[3:5] == ['--mode', 'plan']
    assert kwargs['cwd'] == tmp_path / 'isolated'
    assert kwargs['timeout'] == 600
    assert result['status'] == 'advice_received'


def test_nonzero_cli_does_not_leak_auth_url(tmp_path, monkeypatch):
    monkeypatch.setattr(guardian.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout='secret', stderr='https://login/?token=secret'))
    result = guardian.request_advice({}, tmp_path)
    assert 'secret' not in json.dumps(result)
    assert result['status'] == 'agent_unavailable'


@pytest.mark.parametrize('payload', [{'status': 'ERROR', 'message': 'secret'},
                                     {'is_error': True}, {'error': {'code': 'AUTH_REQUIRED'}}])
def test_cli_error_json_is_not_success_even_with_zero_exit(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(guardian.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    result = guardian.request_advice({}, tmp_path)
    assert result['status'] == 'agent_unavailable'
    assert 'secret' not in json.dumps(result)


def test_repair_cli_uses_accept_edits_without_permission_bypass(tmp_path, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"result":"nothing to change"}')
    monkeypatch.setattr(guardian.subprocess, 'run', run)
    guardian.request_advice({}, tmp_path, repair=True)
    assert calls[0][3:5] == ['--mode', 'accept-edits']
    assert '--dangerously-skip-permissions' not in calls[0]


def test_only_one_instance(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    with FileLock(str(tmp_path / 'guardian_state.json') + '.lock'):
        assert guardian.run_once(tmp_path, now=NOW) == {'status': 'already_running'}


def test_new_archive_triggers_one_daily_improvement(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    archive = tmp_path / 'uploaded_clips'
    archive.mkdir()
    (archive / 'new.mp4').touch()
    calls = []
    monkeypatch.setattr(guardian, 'request_advice', lambda *a, **kw: calls.append(1) or {'status': 'advice_received'})
    guardian.run_once(tmp_path, now=NOW, enable_agent=True)
    (archive / 'second.mp4').touch()
    atomic_io.atomic_write_json(tmp_path / 'orchestrator_state.json',
                               {'status': 'running', 'last_check': (NOW + timedelta(minutes=30)).isoformat()})
    guardian.run_once(tmp_path, now=NOW + timedelta(minutes=30), enable_agent=True)
    assert calls == [1]


def test_repair_workspace_must_not_be_production(tmp_path):
    with pytest.raises(ValueError):
        guardian.validate_repair_workspace(tmp_path, tmp_path / 'nested')


def test_repair_workspace_requires_guardian_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(guardian.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='main'))
    with pytest.raises(ValueError):
        guardian.validate_repair_workspace(tmp_path / 'prod', tmp_path / 'worker')


def test_snapshot_contains_no_filenames_or_state_prompts(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    atomic_io.atomic_write_json(tmp_path / 'optimization_state.json',
                               {'sample_size_total': 9, 'summary_lines': ['SECRET'], 'preferred': {'music_track': 'SECRET'}})
    snapshot = guardian.collect_snapshot(tmp_path, NOW)
    assert snapshot['measured_performance_samples'] == 9
    assert 'SECRET' not in json.dumps(snapshot)


@pytest.mark.parametrize('exit_code,status', [(0, 'tested_candidate'), (1, 'tests_failed')])
def test_repair_candidate_is_independently_tested(tmp_path, monkeypatch, exit_code, status):
    root = tmp_path / 'prod'
    root.mkdir()
    setup_root(root, monkeypatch)
    (root / 'orchestrator_state.json').unlink()
    workspace = tmp_path / 'worker'
    workspace.mkdir()
    monkeypatch.setattr(guardian, 'validate_repair_workspace', lambda *a: None)
    monkeypatch.setattr(guardian, 'request_advice', lambda *a, **kw: {'status': 'advice_received'})
    calls = []
    def run(args, **kw):
        calls.append((args, kw))
        if args[:2] == ['git', 'diff']:
            return SimpleNamespace(returncode=1, stdout='')
        if args[:2] == ['git', 'status']:
            return SimpleNamespace(returncode=0, stdout=' M fix.py')
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(guardian.subprocess, 'run', run)
    result = guardian.run_once(root, now=NOW, enable_agent=True, repair_workspace=workspace)
    assert result['agent']['candidate_status'] == status
    assert calls[-1][0][1:] == ['-m', 'pytest', '-q']
    assert calls[-1][1]['cwd'] == workspace
    assert not (root / 'output').exists()


def test_successful_agent_without_edits_is_no_changes(tmp_path, monkeypatch):
    root = tmp_path / 'prod'
    root.mkdir()
    setup_root(root, monkeypatch)
    (root / 'orchestrator_state.json').unlink()
    workspace = tmp_path / 'worker'
    workspace.mkdir()
    monkeypatch.setattr(guardian, 'validate_repair_workspace', lambda *a: None)
    monkeypatch.setattr(guardian, 'request_advice', lambda *a, **kw: {'status': 'advice_received'})
    def run(args, **kwargs):
        assert args[0] == 'git', 'No independent test run or repair claim for unchanged files'
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setattr(guardian.subprocess, 'run', run)
    result = guardian.run_once(root, now=NOW, enable_agent=True, repair_workspace=workspace)
    assert result['agent']['candidate_status'] == 'no_changes'
    assert 'tests_passed' not in result['agent']
