from concurrent.futures import ThreadPoolExecutor

import process


def test_parallel_clips_with_same_index_keep_their_own_captions(tmp_path, monkeypatch):
    monkeypatch.setattr(process, 'TEMP_DIR', tmp_path)

    def build(number):
        marker = f'STREAMER_{number}'
        clip = {'start_time': 0, 'end_time': 3, 'title': marker}
        transcript = {'segments': [{'start': 0, 'end': 2, 'text': marker}]}
        return marker, process.build_ass_for_clip(clip, transcript, 1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        rendered = list(pool.map(build, range(16)))

    assert len({path for _, path in rendered}) == 16
    for marker, path in rendered:
        content = path.read_text(encoding='utf-8')
        assert marker in content
        assert content.count(',TitleBox,') == 1
        # A subsequent render using identical index/title also needs its own file.
        other = process.build_ass_for_clip({'start_time': 0, 'end_time': 3, 'title': marker}, {'segments': []}, 1)
        assert other != path
        assert path.read_text(encoding='utf-8') == content
