# Local configuration

`streamers.json` is private and ignored by Git. Copy `streamers.example.json` to
`streamers.json` to start a new installation; publishing is disabled in the example.

`profiles/` contains editable streamer profiles. `autoclip` seeds the three packaged
defaults on first start without replacing existing custom profiles.

Credentials belong in `var/secrets/`; API keys and deployment options remain in the
ignored root `.env`. `AUTOCLIP_HOME` selects the application home explicitly.
