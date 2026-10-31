from pathlib import Path
import pytest
import vision
import process

@pytest.mark.parametrize("boxes, passed", [
    ([(10, 10, 20, 20)] * 5, True),
    ([(10, 10, 20, 20)] * 4 + [(200, 200, 20, 20)], True),
    ([(10, 10, 20, 20)] * 2 + [None] + [(10, 10, 20, 20)] * 2, True),
    ([(90, 10, 30, 20)] * 5, False),
    ([(10, 10, 20, 20)] * 3 + [None, None], False),
])
def test_temporal_crop(monkeypatch, boxes, passed):
    calls = []
    def detect(path, ts):
        box = boxes[len(calls)]
        calls.append(ts)
        return ([box], [0.99], 1920, 1080) if box else ([], [], 1920, 1080)
    monkeypatch.setattr(vision, "_detect_faces", detect)
    result = vision.validate_facecam_crop("video", 10, 30, (0, 0, 100, 100))
    assert result["passed"] is passed
    assert len(calls) == 5
    assert all(10 < ts < 30 for ts in calls)
    assert (result["reason"] is None) is passed


def test_read_failure_is_unsafe(monkeypatch):
    def fail(*args):
        raise RuntimeError("decode")
    monkeypatch.setattr(vision, "_detect_faces", fail)
    assert not vision.validate_facecam_crop("video", 0, 10, (0, 0, 100, 100))["passed"]


def test_resolver_falls_back_and_reports_reason(monkeypatch):
    result = {"passed": False, "reason": "facecam_missing_or_outside_crop", "samples": []}
    monkeypatch.setattr(vision, "validate_facecam_crop", lambda *args: result)
    metadata = {}
    layout, _ = process.resolve_layout("split_screen", Path("video"), 0, 20, crop_box=(0, 0, 100, 100), validation_result=metadata)
    assert layout == "blur_background"
    assert metadata == result


@pytest.mark.parametrize("layout", ["center_crop", "blur_background", "multi_cam"])
def test_non_facecam_resolver_does_not_impose_top_face(monkeypatch, layout):
    def unexpected(*args):
        raise AssertionError("facecam validation must only run for a selected facecam crop")
    monkeypatch.setattr(vision, "validate_facecam_crop", unexpected)
    assert process.resolve_layout(layout, Path("video"), 0, 20)[0] == layout


def test_pipeline_records_fallback_in_sidecar(tmp_path, monkeypatch):
    from test_process_layout_selection import _process_clips_iter_common_mocks
    real_resolve = process.resolve_layout
    data = {"clips": [{"start_time": 0.0, "end_time": 20.0, "title": "Clip"}]}
    _process_clips_iter_common_mocks(monkeypatch, tmp_path, data)
    monkeypatch.setattr(process, "resolve_layout", real_resolve)
    monkeypatch.setattr(vision, "locate_persistent_facecam", lambda *args:
                        vision.FacecamScan((.82, .05, .92, .2), 1., 16, 16, .9))
    monkeypatch.setattr(vision, "get_facecam_coordinates", lambda *args, **kwargs: (0, 0, 100, 100))
    monkeypatch.setattr(vision, "validate_facecam_crop", lambda *args: {
        "passed": False, "reason": "facecam_missing_or_outside_crop", "samples": []})
    monkeypatch.setattr(process.auto_qc, "check_render", lambda *args, **kwargs: {"passed": True})
    captured = {}
    monkeypatch.setattr(process, "_write_clip_metadata_sidecar", lambda path, clip: captured.update(clip))
    list(process.process_clips_iter(source_video=tmp_path / "source.mp4", layout="split_screen", output_dir=tmp_path / "out"))
    assert captured["layout"] == "blur_background"
    assert captured["layout_validation"]["reason"] == "facecam_missing_or_outside_crop"
