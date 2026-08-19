"""2026-08-19: a stream drop or invalid input makes ffmpeg fail, and the exception message
stream_watcher.record_stream_chunk() raised used to embed a plain stderr[-2000:] tail — which
does NOT reliably exclude ffmpeg's own build-config banner (a "configuration:" line listing
every --enable-lib... flag, confirmed live to run past 2000 characters by itself) when ffmpeg
fails fast: the whole captured stderr can be shorter than the truncation window, banner
included. That raised message is what auto_pilot.py embeds verbatim into agent_state.json's
current_action, so the banner ended up dumped straight into the dashboard.

REAL_FFMPEG_STDERR below is a live capture (2026-08-19) from `ffmpeg -i <corrupt file>`,
not a hand-written approximation."""

import process

REAL_FFMPEG_STDERR = """ffmpeg version 9.0-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
  built with gcc 16.1.0 (Rev2, Built by MSYS2 project)
  configuration: --enable-gpl --enable-version3 --enable-static --disable-w32threads --disable-autodetect --enable-cairo --enable-fontconfig --enable-iconv --enable-gnutls --enable-lcms2 --enable-libxml2 --enable-gmp --enable-bzlib --enable-lzma --enable-libsnappy --enable-zlib --enable-librist --enable-libsrt --enable-libssh --enable-libzmq --enable-avisynth --enable-libbluray --enable-libcaca --enable-libdvdnav --enable-libdvdread --enable-sdl2 --enable-libaribb24 --enable-libaribcaption --enable-libdav1d --enable-libdavs2 --enable-libopenjpeg --enable-libquirc --enable-libuavs3d --enable-libxevd --enable-libzvbi --enable-liboapv --enable-libqrencode --enable-librav1e --enable-libsvtav1 --enable-libvvenc --enable-libwebp --enable-libx264 --enable-libx265 --enable-libxavs2 --enable-libxeve --enable-libxvid --enable-libaom --enable-libjxl --enable-libsvtjpegxs --enable-libvpx --enable-mediafoundation --enable-libass --enable-frei0r --enable-libfreetype --enable-libfribidi --enable-libharfbuzz --enable-liblensfun --enable-libvidstab --enable-libvmaf --enable-libzimg --enable-amf --enable-cuda-llvm --enable-cuvid --enable-dxva2 --enable-d3d11va --enable-d3d12va --enable-ffnvcodec --enable-libvpl --enable-nvdec --enable-nvenc --enable-vaapi --enable-vulkan --enable-libplacebo --enable-opencl --enable-libcdio --enable-openal --enable-libgme --enable-libmodplug --enable-libopenmpt --enable-libopencore-amrwb --enable-libmp3lame --enable-libshine --enable-libtheora --enable-libtwolame --enable-libvo-amrwbenc --enable-libcodec2 --enable-libilbc --enable-libgsm --enable-liblc3 --enable-libopencore-amrnb --enable-libopus --enable-libspeex --enable-libvorbis --enable-ladspa --enable-libbs2b --enable-libflite --enable-libmysofa --enable-librubberband --enable-libsoxr --enable-chromaprint --enable-whisper
  libavutil      61.  1.100 / 61.  1.100
  libavcodec     63.  1.100 / 63.  1.100
  libavformat    63.  1.100 / 63.  1.100
  libavdevice    63.  1.100 / 63.  1.100
  libavfilter    12.  1.100 / 12.  1.100
  libswscale     10.  1.100 / 10.  1.100
  libswresample   7.  1.100 /  7.  1.100
[in#0 @ 00000202af830a40] Error opening input: Invalid data found when processing input
Error opening input file C:/Users/yunus/scratch/corrupt_input.ts.
Error opening input files: Invalid data found when processing input
"""


def test_real_stderr_sample_is_actually_longer_than_a_naive_2000_char_tail():
    # Sanity check on the fixture itself: the old approach (a plain stderr[-2000:] tail) would
    # still have included a chunk of raw "--enable-libxxx" banner noise even in the LAST 2000
    # characters of this real capture — proving position-based truncation doesn't reliably
    # exclude the banner, which is the actual bug this fixture exists to guard against.
    assert "--enable-" in REAL_FFMPEG_STDERR[-2000:]


def test_extracts_only_the_real_error_lines():
    summary = process.extract_ffmpeg_error_summary(REAL_FFMPEG_STDERR)
    assert "configuration:" not in summary
    assert "--enable-" not in summary
    assert "libavutil" not in summary
    assert "Invalid data found when processing input" in summary


def test_summary_is_short():
    summary = process.extract_ffmpeg_error_summary(REAL_FFMPEG_STDERR)
    assert len(summary) < 300  # nowhere near the 2000+ char banner


def test_respects_max_lines():
    summary = process.extract_ffmpeg_error_summary(REAL_FFMPEG_STDERR, max_lines=1)
    assert summary.count("\n") == 0
    assert "Error opening input files" in summary


def test_empty_stderr_returns_empty_string():
    assert process.extract_ffmpeg_error_summary("") == ""
    assert process.extract_ffmpeg_error_summary(None) == ""


def test_stderr_with_no_banner_still_works():
    summary = process.extract_ffmpeg_error_summary("Some unexpected one-line failure")
    assert summary == "Some unexpected one-line failure"
