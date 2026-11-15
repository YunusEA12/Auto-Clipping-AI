from pathlib import Path

import pytest

from autoclip.dashboard import dashboard_api as api


def test_archive_is_bounded_and_has_stable_pages(tmp_path):
    for index in range(20):
        (tmp_path / f"clip_{index:02}.mp4").write_bytes(b"video")
    first, total = api.archive_page(1, root=tmp_path)
    second, _ = api.archive_page(2, root=tmp_path)
    assert total == 20 and len(first) == len(second) == 6
    assert set(first).isdisjoint(second)


def test_download_does_not_load_video_until_clicked(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Path, "read_bytes", lambda path: calls.append(path) or b"video")
    reader = api.clip_download(tmp_path / "large.mp4")
    assert calls == []
    assert reader() == b"video"
    assert len(calls) == 1


def test_dashboard_refuses_to_delete_media_outside_output(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    with pytest.raises(ValueError):
        api.delete_local_clip(source)
    assert source.read_bytes() == b"original"


def test_manual_tiktok_upload_uses_fleet_safeguards(monkeypatch, tmp_path):
    from autoclip.publishing import upload_manager

    calls = []
    monkeypatch.setattr(upload_manager, "upload_tiktok_clip", lambda *a, **k: calls.append(k) or "guarded-outcome")
    assert api.upload_clip_to_tiktok(tmp_path / "clip.mp4", "description", publish=True) == "guarded-outcome"
    assert calls == [{"publish": True}]


def test_source_uploads_never_overwrite_existing_files_or_escape_temp():
    from autoclip import runtime

    first = api.store_source_upload("../../source.mp4", b"first original")
    second = api.store_source_upload("source.mp4", b"different original")
    assert first != second
    assert first.resolve().is_relative_to(runtime.path("temp").resolve())
    assert first.read_bytes() == b"first original"
    assert second.read_bytes() == b"different original"


def test_invalid_metadata_is_unknown_instead_of_assigned_from_another_clip(tmp_path):
    video = tmp_path / "clip.mp4"
    video.with_suffix(".json").write_text('["not a clip"]')
    assert api.clip_metadata(video) is None
