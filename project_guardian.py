"""Read-only project watchdog with optional subscription-authenticated Antigravity advice.

No Gemini API client, uploads, restarts, source edits or deployment are performed here.
The CLI must already be configured for personal Google-account authentication.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

from filelock import FileLock, Timeout
import psutil

import atomic_io
import llm_utils
import qc_summary

MAX_AGENT_RUNS_PER_DAY = 8
AGENT_TIMEOUT_SECONDS = 600
ISSUE_RETRY_SECONDS = 3600
KNOWN_REASONS = {'black_frames', 'frozen_frames', 'loudness', 'upper_face', 'subtitles_safezone'}


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _number(value, default=0):
    return value if type(value) in (int, float) else default


def collect_snapshot(root: Path, now: datetime) -> dict:
    """Whitelisted numeric facts only; never includes source text or credentials."""
    issues = []
    paused = _read(root / 'fleet_control.json').get('target_state') == 'paused'
    orchestrator = _read(root / 'orchestrator_state.json')
    if not paused:
        try:
            checked = datetime.fromisoformat(orchestrator['last_check'])
            threshold = max(600, _number(orchestrator.get('poll_interval'), 90) * 4)
            stale = checked.tzinfo is None or (now - checked).total_seconds() > threshold
        except (KeyError, ValueError, TypeError):
            stale = True
        if stale or orchestrator.get('status') != 'running':
            issues.append('orchestrator_stale_or_stopped')
    disk = psutil.disk_usage(str(root))
    if disk.free < 2 * 1024 ** 3 or disk.percent >= 95:
        issues.append('disk_space_low')
    try:
        qc = qc_summary.summarize(root / 'output', 50)
        reasons = {}
        for key, count in qc['reasons'].items():
            safe_key = key if key in KNOWN_REASONS else 'other'
            reasons[safe_key] = reasons.get(safe_key, 0) + count
        qc['reasons'] = reasons
        if qc['evaluated'] >= 5 and qc['blocked_fraction'] >= .5:
            issues.append('qc_block_rate_high')
        if qc['invalid_reports']:
            issues.append('invalid_qc_reports')
    except OSError:
        qc = {'status': 'unavailable'}
        issues.append('qc_scan_unavailable')
    # Include archive changes: successful clips leave output/, so its QC counts alone
    # cannot tell whether another day of real production has happened.
    clip_stamps = []
    for directory in ('output', 'uploaded_clips'):
        for path in (root / directory).rglob('*.mp4'):
            try:
                stat = path.stat()
                clip_stamps.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
    clip_stamps.sort(reverse=True)
    signature = hashlib.sha256(json.dumps(clip_stamps[:50]).encode()).hexdigest()
    optimization = _read(root / 'optimization_state.json')
    return {'fleet_paused': paused, 'issues': sorted(issues), 'qc': qc,
            'free_disk_gib': round(disk.free / 1024 ** 3, 1),
            'clip_count': len(clip_stamps), 'clip_signature': signature,
            'measured_performance_samples': _number(optimization.get('sample_size_total')),
            'limitations': ['No watchtime or retention data', 'Advice is not model training']}


def subscription_environment() -> dict:
    """Allowlist OS/session variables; never forward API keys or cloud auth switches."""
    allowed = {'PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMROOT',
               'WINDIR', 'TEMP', 'TMP', 'USER', 'LOGNAME', 'SHELL', 'TERM', 'LANG',
               'LC_ALL', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
               'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def request_advice(snapshot: dict, workspace: Path, executable: str = 'agy', *, repair: bool = False) -> dict:
    workspace.mkdir(parents=True, exist_ok=True)
    prompt = (
        'Du bist der Wartungsberater fuer Auto-Clipping-AI. Nutze ausschliesslich die '
        'folgenden aggregierten Messdaten. Keine Tools, Dateizugriffe, Shell-Befehle, '
        'Uploads, Netzwerkrecherchen oder Aenderungen ausfuehren. Liefere eine kurze '
        'deutsche Diagnose und drei priorisierte, testbare Verbesserungen. '
        'Eine pausierte Fleet ist absichtlich pausiert. Fehlende Daten nicht erfinden. '
        'Musikauswahl nur aus gemessenen Ergebnissen lernen, keine Rechte behaupten. '
        'Codekorrekturen benoetigen spaeter einen separaten Checkout, Regressionstests '
        'und den vollstaendig gruenen pytest-Lauf vor Uebernahme. '
        'Keine API-Schluessel verwenden oder Zusatzcredits aktivieren. DATEN:\n' +
        json.dumps(snapshot, ensure_ascii=False)
    )
    if repair:
        prompt = (
            'Arbeite ausschliesslich in diesem separaten Git-Checkout. Lies claude.md. '
            'Untersuche die folgenden aggregierten Betriebsdaten, finde hoechstens einen '
            'konkreten reproduzierbaren Fehler und implementiere eine kleine Korrektur '
            'mit Regressionstest. Fuehre pytest aus. Keine API-Aufrufe, Browser, Uploads, '
            'Passwoerter, Produktivdateien, Quellmedien, Service-Neustarts, git push, '
            'Commits, Merges oder Deployment. Keine Tests entfernen oder Qualitaetssperren '
            'abschalten. Wenn kein Fehler reproduzierbar ist: keine spekulativen '
            'Aenderungen, nur Bericht. Musikempfehlungen benoetigen echte Messdaten. '
            'Berichte Aenderungen und offene Grenzen. DATEN:\n' + json.dumps(snapshot)
        )
    mode = 'accept-edits' if repair else 'plan'
    completed = subprocess.run([executable, '-p', prompt, '--mode', mode, '--output-format', 'json'],
                               cwd=workspace, env=subscription_environment(),
                               capture_output=True, text=True, timeout=AGENT_TIMEOUT_SECONDS)
    if completed.returncode:
        # Raw CLI diagnostics can contain authorization links or token material.
        return {'status': 'agent_unavailable', 'exit_code': completed.returncode,
                'action': 'Check subscription sign-in and CLI permissions interactively'}
    try:
        data = json.loads(completed.stdout)
    except (ValueError, TypeError):
        return {'status': 'agent_invalid_response'}
    if not isinstance(data, dict):
        return {'status': 'agent_invalid_response'}
    if (str(data.get('status', '')).upper() in {'ERROR', 'FAILED', 'FAILURE'}
            or data.get('is_error') is True or data.get('error')):
        return {'status': 'agent_unavailable',
                'action': 'Check subscription sign-in and CLI permissions interactively'}
    return {'status': 'advice_received',
            'response': llm_utils.redact_secrets(json.dumps(data, ensure_ascii=False))[:12000]}


def validate_repair_workspace(root: Path, workspace: Path) -> None:
    """Opt-in repairs require a clean, separate checkout on a guardian branch."""
    workspace = workspace.resolve()
    if workspace == root or root in workspace.parents or workspace in root.parents:
        raise ValueError('Repair workspace must be separate from production')
    for args, predicate in [
        (['git', 'symbolic-ref', '--short', 'HEAD'], lambda s: s.startswith('codex/guardian')),
        (['git', 'status', '--porcelain'], lambda s: not s),
    ]:
        value = subprocess.run(args, cwd=workspace, capture_output=True, text=True, timeout=15)
        if value.returncode or not predicate(value.stdout.strip()):
            raise ValueError('Repair workspace must be clean and on codex/guardian*')


def run_once(root: Path, *, enable_agent: bool = False, executable: str = 'agy',
             now: datetime | None = None, repair_workspace: Path | None = None) -> dict:
    root = root.resolve()
    now = now or datetime.now(timezone.utc)
    state_path = root / 'guardian_state.json'
    try:
        with FileLock(str(state_path) + '.lock', timeout=0):
            previous = _read(state_path)
            today = now.date().isoformat()
            runs = previous.get('agent_runs', 0) if previous.get('day') == today else 0
            runs = max(0, int(_number(runs)))
            snapshot = collect_snapshot(root, now)
            try:
                last_attempt = datetime.fromisoformat(previous['last_attempt_at'])
                retry_due = last_attempt.tzinfo is None or (now - last_attempt).total_seconds() >= ISSUE_RETRY_SECONDS
            except (KeyError, TypeError, ValueError):
                retry_due = True
            issue_due = bool(snapshot['issues']) and (
                snapshot['issues'] != previous.get('last_attempt_issues') or retry_due)
            improvement_due = (not snapshot['fleet_paused'] and snapshot['clip_count'] > 0
                               and previous.get('improvement_day') != today
                               and snapshot['clip_signature'] != previous.get('advised_clip_signature')
                               and retry_due)
            report = {'checked_at': now.isoformat(), 'snapshot': snapshot,
                      'status': 'attention' if snapshot['issues'] else 'healthy',
                      'agent': {'status': 'disabled' if not enable_agent else 'not_needed'}}
            state = {**previous, 'day': today, 'agent_runs': runs,
                     'issues': snapshot['issues'], 'checked_at': now.isoformat()}
            if enable_agent and (issue_due or (not snapshot['issues'] and improvement_due)):
                if runs >= MAX_AGENT_RUNS_PER_DAY:
                    report['agent'] = {'status': 'daily_limit_reached'}
                else:
                    # Reserve before subprocess start: crashes/timeouts cannot reset quota.
                    state['agent_runs'] = runs + 1
                    state['last_attempt_at'] = now.isoformat()
                    state['last_attempt_issues'] = snapshot['issues']
                    atomic_io.atomic_write_json(state_path, state)
                    try:
                        workspace = root / 'guardian_workspace'
                        if repair_workspace is not None:
                            validate_repair_workspace(root, repair_workspace)
                            workspace = repair_workspace.resolve()
                        report['agent'] = request_advice(snapshot, workspace, executable,
                                                        repair=repair_workspace is not None)
                        if repair_workspace is not None and report['agent']['status'] == 'advice_received':
                            diff = subprocess.run(['git', 'diff', '--quiet', 'HEAD'], cwd=workspace,
                                                  capture_output=True, text=True, timeout=15)
                            status = subprocess.run(['git', 'status', '--porcelain'], cwd=workspace,
                                                    capture_output=True, text=True, timeout=15)
                            if diff.returncode not in (0, 1) or status.returncode:
                                raise ValueError('Cannot verify candidate diff')
                            changed = diff.returncode == 1 or bool(status.stdout.strip())
                            if changed:
                                tests = subprocess.run([sys.executable, '-m', 'pytest', '-q'],
                                                       cwd=workspace, env=subscription_environment(),
                                                       capture_output=True, text=True, timeout=300)
                                report['agent']['tests_passed'] = tests.returncode == 0
                                report['agent']['candidate_status'] = ('tested_candidate' if tests.returncode == 0
                                                                        else 'tests_failed')
                            else:
                                report['agent']['candidate_status'] = 'no_changes'
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        report['agent'] = {'status': 'agent_unavailable', 'error_type': type(exc).__name__}
                    except ValueError:
                        report['agent'] = {'status': 'invalid_repair_workspace'}
                    if report['agent']['status'] == 'advice_received':
                        state.update(improvement_day=today,
                                     advised_issues=snapshot['issues'],
                                     advised_clip_signature=snapshot['clip_signature'])
            atomic_io.atomic_write_json(state_path, state)
            reports = root / 'guardian_reports'
            reports.mkdir(exist_ok=True)
            atomic_io.atomic_write_json(reports / 'latest.json', report)
            return report
    except Timeout:
        return {'status': 'already_running'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--enable-agent', action='store_true')
    parser.add_argument('--agy', default='agy')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--repair-workspace', type=Path)
    parser.add_argument('--interval', type=int, default=1800)
    args = parser.parse_args()
    if args.interval < 60:
        parser.error('--interval must be at least 60 seconds')
    while True:
        report = run_once(args.root, enable_agent=args.enable_agent, executable=args.agy,
                          repair_workspace=args.repair_workspace)
        print(json.dumps({'status': report['status'], 'agent': report.get('agent', {}).get('status')}), flush=True)
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
