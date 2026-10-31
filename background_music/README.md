# Background music tracks

`process.py`'s `pick_background_track()` picks one file from this folder at random and mixes
it quietly under each clip's own audio (`build_audio_filter()`), via ffmpeg's `amix`/`volume`
filters. If this folder is empty or missing, clips render exactly as before — no music, no
error.

## What belongs here

Short (a minute or more is plenty — it loops if the clip runs longer), royalty-free or
explicitly-licensed instrumental tracks. `.mp3`, `.m4a`, `.aac`, `.wav`, `.ogg` are picked up;
anything else is ignored.

**You are responsible for the license of every file you put here.** This pipeline uploads
publicly to real YouTube/TikTok accounts — a track without a clear royalty-free/Creative
Commons license (or one that requires attribution you're not adding) can get a video claimed,
muted, or taken down after the fact. Some starting points: YouTube's own Audio Library
(studio.youtube.com > Audio Library > download), Pixabay Music, or a Creative Commons search
filtered to commercial-use-allowed, attribution-not-required licenses.

## The three tracks currently here

`ambient_pad_c_major.mp3`, `chill_pad_a_minor.mp3`, `lofi_drift_g_minor.mp3` (2026-08-21) are
**procedurally synthesized from scratch via ffmpeg** — layered sine-wave triads shaped with
`tremolo`/`vibrato`/`lowpass`/`aecho` into lo-fi/ambient-style pads, not sampled, downloaded,
or derived from any existing recording. No third-party license applies because there's no
third party: this is the safest possible copyright position for content that uploads
automatically and unattended, at the cost of sounding more like a generic synth pad than a
produced lo-fi track. Swap in real licensed music any time — see above for where to find it —
this was a deliberate "verifiably safe over polished" tradeoff, not a permanent choice.

## Mood tags (`tags.json`)

`tags.json` maps each file name to `{"energy": 1-10, "moods": [...]}`. `pick_background_track(clip)`
(via `music_matching.py`) uses it to weight the random choice by how well a track fits the clip:
the clip's measured `energy_rating`, its `hook_style` and keywords in title/hook. Untagged tracks
get a neutral weight; no track ever drops to zero, so the epsilon-greedy learning
(`optimization_engine.preferred_music_track`) keeps sampling everything. **Add a tags entry for every
new file.** Known moods: `chill`, `ambient`, `mysterious`, `upbeat`, `neutral`, `energetic`,
`intense`, `dramatic`, `funny`.

## Tracks that should be added (royalty-free, license checked by you)

No files are downloaded automatically. Suggested additions, with tags:

| Wanted track type | energy | moods |
|---|---|---|
| Driving electronic / phonk-light beat (clutch, fails, big plays) | 8-9 | `energetic`, `intense` |
| Cinematic tension riser / dark pulse (shock moments, reveals) | 6-7 | `dramatic`, `mysterious` |
| Upbeat playful chiptune or pop-instrumental (funny moments) | 6 | `upbeat`, `funny` |
| Mellow acoustic / soft piano (stories, calm talk) | 2-3 | `chill`, `neutral` |
| Dark ambient drone (curiosity-gap hooks) | 3 | `mysterious`, `ambient` |
