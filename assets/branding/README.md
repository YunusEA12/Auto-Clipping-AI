# ClipMaster

Approved direction: modern and clear. Original white CM monogram, mint play symbol,
charcoal background; no third-party platform marks or streamer portraits.

`profile.json` is the source of truth for the bio and intended account. The bio is
75 characters, including the line break. `clipmaster-avatar.png` was generated with
the built-in Imagegen tool and is the final project asset.

Prompt used:

> A modern, clear TikTok profile avatar for the independent German Twitch-highlight
> channel ClipMaster. Bold white geometric CM monogram with an integrated small mint
> play triangle on deep charcoal. Centered, generous circular-crop margins, legible at
> 40 pixels. Subtle dimensional lighting, crisp edges. Only CM lettering. No streamer
> faces, TikTok/Twitch logos, stock mascot, slogan or watermark.

Prepare with `uv run python scripts/generate_profile_assets.py --force`. The prepared
files and private verification screenshots go to `var/profile_assets/`. Apply with
`uv run python scripts/setup_tiktok_profile.py --headed`. TikTok may require a human
captcha. Saving is verified from the profile; a successful click alone is insufficient.
