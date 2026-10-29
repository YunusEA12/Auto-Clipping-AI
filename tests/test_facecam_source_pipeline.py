"""Regression: a real face in watched content is not sufficient for a second panel."""
import pytest
import process
import vision
from test_process_layout_selection import _process_clips_iter_common_mocks


@pytest.mark.parametrize('scan,reason', [
    (vision.FacecamScan((.38, .2, .62, .6), 1., 16, 16, .99), 'geometry'),
    (vision.FacecamScan(None, 0., 0, 0, 0.), 'scan'),
])
def test_forced_split_cannot_duplicate_interview_face(tmp_path, monkeypatch, scan, reason):
    data = {'clips': [{'start_time': 0., 'end_time': 20., 'title': 'Interview'}]}
    _process_clips_iter_common_mocks(monkeypatch, tmp_path, data)
    monkeypatch.setattr(vision, 'locate_persistent_facecam', lambda *a: scan)
    # Preserve real layout selection; forced split must be guarded too.
    monkeypatch.setattr(process, 'resolve_layout', lambda layout, *a, **kw: (layout, 0.))
    monkeypatch.setattr(vision, 'get_facecam_coordinates', lambda *a, **kw: pytest.fail('Untrusted face must not be cropped'))
    monkeypatch.setattr(process.auto_qc, 'check_render', lambda *a, **kw: {'passed': True, 'checks': {}})
    captured = {}
    monkeypatch.setattr(process, '_write_clip_metadata_sidecar', lambda path, clip: captured.update(clip))
    rendered = []
    monkeypatch.setattr(process, 'render_clip', lambda *args, **kwargs: rendered.append(args) or tmp_path / 'clip.mp4')
    list(process.process_clips_iter(source_video=tmp_path/'source.mp4', layout='split_screen', output_dir=tmp_path/'out'))
    assert rendered[0][4] == 'blur_background'
    assert captured['layout'] == 'blur_background'
    assert not captured['facecam_source']['passed']
    assert captured['layout_validation']['reason']
    assert captured['auto_qc']['checks']['facecam_source']['applicable'] is False


def test_configured_camera_cannot_be_overridden_by_interview_face(tmp_path, monkeypatch):
    data = {'clips': [{'start_time': 0., 'end_time': 20., 'title': 'Reaction'}]}
    _process_clips_iter_common_mocks(monkeypatch, tmp_path, data)
    monkeypatch.setattr(vision, 'locate_persistent_facecam', lambda *a: vision.FacecamScan((.4,.2,.6,.6),1.,16,16,.99))
    monkeypatch.setattr(process.streamers, 'load_streamers', lambda: [{'name':'coachlim', 'facecam_box':[.8,0.,1.,.3]}])
    calls = []
    monkeypatch.setattr(vision, 'get_facecam_coordinates', lambda *a, **kw: calls.append(kw) or (1500,0,420,324))
    monkeypatch.setattr(vision, 'validate_facecam_crop', lambda *a: {'passed':True,'reason':None})
    monkeypatch.setattr(vision, 'box_is_mostly_black', lambda *a: False)
    monkeypatch.setattr(process.auto_qc, 'check_render', lambda *a, **kw: {'passed':True})
    results = list(process.process_clips_iter(source_video=tmp_path/'source.mp4', layout='split_screen', output_dir=tmp_path/'out', streamer_name='coachlim'))
    assert calls[0]['configured_roi_only'] is True
    assert results[0][2]['facecam_source']['source'] == 'configured_region'
