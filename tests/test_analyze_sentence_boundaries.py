import pytest
import analyze


def clip(start, end):
    return analyze.Clip(start_time=start, end_time=end, title='test', hook_explanation='x', hook_style='other', viral_score=5, energy_rating=5, description='x', hashtags=['#fy'])


def align(start, end, segments, duration=60):
    return analyze.align_clip_boundaries(analyze.ClipSelection(clips=[clip(start, end)]), {'segments': segments}, duration).clips


def test_mid_word_edges_expand_to_sentence_using_word_timestamps():
    segments = [{'start': 0, 'end': 40, 'words': [
        {'start': 0, 'end': 2, 'text': 'Vorher.'},
        {'start': 3, 'end': 5, 'text': 'Anfang'},
        {'start': 5, 'end': 21, 'text': 'Satzende!'},
        {'start': 22, 'end': 40, 'text': 'Danach.'},
    ]}]
    result = align(4, 19, segments)[0]
    assert result.start_time == pytest.approx(2.7)
    assert result.end_time == pytest.approx(21.5)


def test_segment_fallback_and_padding_clamped_to_source():
    result = align(1, 19, [{'start': 0, 'end': 20}], duration=20)[0]
    assert (result.start_time, result.end_time) == (0, 20)


def test_sentence_expansion_rechecks_max_duration():
    assert align(1, 149, [{'start': 0, 'end': 160}], duration=170) == []


def test_alignment_rechecks_min_duration():
    assert align(1, 5, [{'start': 0, 'end': 6}]) == []


def test_hallucinated_source_time_is_not_rescued_by_alignment():
    assert align(100, 120, [{'start': 0, 'end': 20}]) == []


def test_sentence_punctuation_with_closing_quote():
    words = [{'start': 0, 'end': 20, 'word': 'Satz!"'}, {'start': 21, 'end': 40, 'word': 'Weiter.'}]
    result = align(1, 19, [{'start': 0, 'end': 40, 'words': words}])[0]
    assert result.end_time == pytest.approx(20.5)


def test_analyze_persists_aligned_edges_with_actual_wav_duration(tmp_path, monkeypatch):
    import json
    import wave
    transcript_path = tmp_path / 'sample_transcription.json'
    transcript_path.write_text(json.dumps({'segments': [{'start': 0, 'end': 20, 'text': 'Ein Satz.'}]}), encoding='utf-8')
    audio_path = tmp_path / 'sample.wav'
    with wave.open(str(audio_path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(10)
        wav.writeframes(b'\0\0' * 300)
    monkeypatch.setattr(analyze, 'TEMP_DIR', tmp_path)
    monkeypatch.setattr(analyze, 'scan_energy_windows', lambda _: [])
    monkeypatch.setattr(analyze, 'select_clips', lambda *args, **kwargs: analyze.ClipSelection(clips=[clip(1, 19)]))
    path = analyze.analyze(transcript_path, audio_path=audio_path)
    saved = json.loads(path.read_text(encoding='utf-8'))['clips'][0]
    assert saved['start_time'] == 0
    assert saved['end_time'] == pytest.approx(20.5)
