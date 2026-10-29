import json
import shutil
import subprocess
from pathlib import Path

import pytest

import atomic_io
import auto_qc
import auto_pilot
import dashboard_api
import process
import upload_manager


@pytest.fixture
def ass(tmp_path, monkeypatch):
    monkeypatch.setattr(process, 'TEMP_DIR', tmp_path)
    return process.build_ass_for_clip(
        {'start_time': 0, 'end_time': 3, 'title': 'Test'},
        {'segments': [{'start': 0, 'end': 3, 'text': 'HALLO WELT'}]}, 1)


@pytest.fixture
def measured(monkeypatch):
    values = {'duration': 30, 'lufs': -14., 'black_seconds': 0., 'longest_freeze_seconds': 0.}
    monkeypatch.setattr(auto_qc, 'measure_media', lambda _: values)
    monkeypatch.setattr(auto_qc.vision, 'detect_faces_for_layout', lambda *_: (1, (100, 100, 200, 200), 1080, 1920))
    return values


def test_healthy_render_passes_and_five_face_samples(ass, measured):
    result = auto_qc.check_render(Path('fake.mp4'), layout='split_screen', subtitle_path=ass)
    assert result['passed']
    assert result['checks']['upper_face']['present'] == [True] * 5
    assert result['checks']['subtitles_safezone']['method'] == 'conservative_ass_geometry'


@pytest.mark.parametrize('field,value,reason', [
    ('black_seconds', .5, 'black_frames'),
    ('longest_freeze_seconds', 2., 'frozen_frames'),
    ('lufs', -23., 'loudness'),
    ('lufs', -9., 'loudness'),
])
def test_bad_measurements_fail(ass, measured, field, value, reason):
    measured[field] = value
    result = auto_qc.check_render(Path('fake.mp4'), layout='split_screen', subtitle_path=ass)
    assert not result['passed']
    assert reason in result['reasons']


def test_upper_face_must_be_present_in_four_samples(ass, measured, monkeypatch):
    samples = iter([(1, (0, 0, 20, 20), 1080, 1920)] * 3 + [(0, None, 1080, 1920)] * 2)
    monkeypatch.setattr(auto_qc.vision, 'detect_faces_for_layout', lambda *_: next(samples))
    result = auto_qc.check_render(Path('fake.mp4'), layout='split_screen', subtitle_path=ass)
    assert result['reasons'] == ['upper_face']


def test_face_only_in_lower_half_fails(ass, measured, monkeypatch):
    monkeypatch.setattr(auto_qc.vision, 'detect_faces_for_layout', lambda *_: (1, (100, 1400, 200, 200), 1080, 1920))
    assert 'upper_face' in auto_qc.check_render(Path('x'), layout='split_screen', subtitle_path=ass)['reasons']


def test_safe_fallback_does_not_require_upper_face(ass, measured, monkeypatch):
    monkeypatch.setattr(auto_qc.vision, 'detect_faces_for_layout', lambda *_: pytest.fail('No upper face promised'))
    assert auto_qc.check_render(Path('x'), layout='blur_background', subtitle_path=ass)['passed']


def test_subtitle_outside_safe_zone_fails(ass):
    atomic_io.atomic_write_text(ass, ass.read_text(encoding='utf-8').replace(r'\pos(540,1497)', r'\pos(1050,1850)'))
    assert not auto_qc.check_subtitles(ass)['passed']


def test_probe_error_is_not_a_pass(ass, monkeypatch):
    def error(_):
        raise ValueError('cannot decode')
    monkeypatch.setattr(auto_qc, 'measure_media', error)
    result = auto_qc.check_render(Path('x'), layout='split_screen', subtitle_path=ass)
    assert not result['passed']
    assert result['reasons'] == ['measurement_error: ValueError']


@pytest.mark.parametrize('bad', [None, {}, {'passed': False}, {'passed': 'true'}])
def test_failed_or_invalid_qc_blocks(bad):
    assert auto_qc.metadata_blocked({'auto_qc': bad})


def test_all_platforms_blocked_before_network(tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.touch()
    atomic_io.atomic_write_json(path.with_suffix('.json'), {'auto_qc': {'passed': False}})
    monkeypatch.setattr(upload_manager, '_upload_to_tiktok', lambda *_: pytest.fail('must not upload'))
    result = upload_manager.upload_clip_everywhere(path, 'x', 'x', publish=True)
    assert not result.any_success
    assert not result.youtube.attempted
    assert not result.instagram.attempted


def test_backlog_and_retention_keep_failed_clip_local(tmp_path):
    path = tmp_path / 'clip.mp4'
    path.touch()
    atomic_io.atomic_write_json(path.with_suffix('.json'), {'reward_score': 9, 'auto_qc': {'passed': False}})
    assert auto_pilot.find_backlog_clips(tmp_path, set()) == []
    assert auto_pilot.purge_old_local_only_clips(tmp_path, retention_days=-1) == 0
    assert path.exists()
    assert len(dashboard_api.list_auto_qc_failures(tmp_path)) == 1


def test_low_critic_score_cannot_delete_qc_failure(tmp_path):
    from types import SimpleNamespace
    clip = {'title': 'bad', 'auto_qc': {'passed': False}}
    clips = tmp_path / 'clips.json'
    atomic_io.atomic_write_json(clips, {'clips': [clip]})
    video = tmp_path / 'clip.mp4'
    video.touch()
    batch = SimpleNamespace(verdicts=[SimpleNamespace(clip_title='bad', reward_score=0)])
    kept, deleted, survivors = auto_pilot.purge_low_scoring_clips(batch, {'bad': video}, clips, 5)
    assert (kept, deleted, survivors) == (1, 0, [])
    assert video.exists()


def test_ffmpeg_parser_including_unfinished_freeze(monkeypatch):
    outputs = iter([json.dumps({'format': {'duration': '5'}, 'streams': [{'codec_type': 'audio'}]}),
                    'black_duration:0.75\nfreeze_start: 1.5\n{"input_i":"-14.2"}'])
    monkeypatch.setattr(auto_qc, '_run', lambda _: next(outputs))
    assert auto_qc.measure_media(Path('x')) == {'duration': 5., 'lufs': -14.2, 'black_seconds': .75, 'longest_freeze_seconds': 3.5}


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_real_ffmpeg_motion_and_normal_audio_pass(tmp_path, ass):
    video = tmp_path / 'real.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=108x192:rate=10',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '3',
                    '-af', 'loudnorm=I=-14:TP=-1:LRA=11', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', str(video)], check=True, capture_output=True, timeout=30)
    result = auto_qc.check_render(video, layout='blur_background', subtitle_path=ass)
    assert result['passed'], result


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_real_black_frozen_stream_is_rejected(tmp_path, ass):
    video = tmp_path / 'black.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:size=108x192:rate=10',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '3',
                    '-af', 'loudnorm=I=-14:TP=-1:LRA=11', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', str(video)], check=True, capture_output=True, timeout=30)
    result = auto_qc.check_render(video, layout='blur_background', subtitle_path=ass)
    assert not result['passed']
    assert 'black_frames' in result['reasons']
    assert 'frozen_frames' in result['reasons']


def test_corrupt_sidecar_blocks_upload(tmp_path):
    video = tmp_path / 'bad.mp4'
    atomic_io.atomic_write_text(video.with_suffix('.json'), '{broken')
    assert auto_qc.upload_blocked(video)


def test_silent_audio_measurement_fails_closed(ass, monkeypatch):
    outputs = iter([json.dumps({'format': {'duration': '5'}, 'streams': [{'codec_type': 'audio'}]}),
                    '{"input_i":"-inf"}'])
    monkeypatch.setattr(auto_qc, '_run', lambda _: next(outputs))
    assert not auto_qc.check_render(Path('x'), layout='blur_background', subtitle_path=ass)['passed']


def test_archive_retains_qc_and_layout_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace
    video = tmp_path / 'clip.mp4'
    video.touch()
    clip = {'title': 'test', 'auto_qc': {'passed': True, 'checks': {'loudness': {'integrated_lufs': -14.}}},
            'layout_validation': {'reason': 'facecam moved'}}
    monkeypatch.setattr(auto_pilot, 'UPLOADED_CLIPS_DIR', tmp_path / 'archive')
    monkeypatch.setattr(upload_manager, 'upload_clip_everywhere', lambda *a, **kw: SimpleNamespace(
        tiktok=SimpleNamespace(success=True, confirmed=True),
        youtube=SimpleNamespace(attempted=False, success=False, video_id=None, url=None),
        instagram=SimpleNamespace(attempted=False, success=False, confirmed=False)))
    assert auto_pilot.run_deployment_phase([(clip, video, 9)], publish=True) == (1, 0)
    archived = json.loads((tmp_path / 'archive' / 'clip.json').read_text(encoding='utf-8'))
    assert archived['auto_qc'] == clip['auto_qc']
    assert archived['layout_validation'] == clip['layout_validation']
