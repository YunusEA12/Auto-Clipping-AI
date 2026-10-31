import os
import atomic_io
from qc_summary import summarize


def test_newest_recursive_clips_and_missing_reports(tmp_path):
    folder = tmp_path / 'streamer'
    folder.mkdir()
    data = [None, {'passed': True}, {'passed': False, 'reasons': ['loudness', 'loudness']},
            {'passed': False, 'reasons': ['loudness', 'black_frames']}]
    for index, qc in enumerate(data):
        path = folder / f'{index}.mp4'
        path.touch()
        os.utime(path, (index + 100, index + 100))
        if qc is not None:
            atomic_io.atomic_write_json(path.with_suffix('.json'), {'auto_qc': qc})
    result = summarize(tmp_path, 3)
    assert result['total'] == 3
    assert result['blocked_fraction'] == 2 / 3
    assert result['reasons'] == {'loudness': 2, 'black_frames': 1}
    assert summarize(tmp_path, 4)['missing_qc'] == 1


def test_empty_and_corrupt_reports(tmp_path):
    assert summarize(tmp_path)['blocked_fraction'] is None
    video = tmp_path / 'bad.mp4'
    video.touch()
    atomic_io.atomic_write_text(video.with_suffix('.json'), '{broken')
    result = summarize(tmp_path)
    assert result['invalid_reports'] == 1
    assert result['evaluated'] == 0
