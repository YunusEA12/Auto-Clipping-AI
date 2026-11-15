import json
from pathlib import Path

from scripts import setup_tiktok_profile as profile


def test_brand_copy_fits_platform_limit_and_has_specific_target():
    source = Path(__file__).parents[1] / "assets/branding"
    copy = json.loads((source / "profile.json").read_text(encoding="utf-8"))
    assert copy["expected_handle"] == "clipmaster6152"
    assert len(copy["bio"]) <= copy["bio_limit"] == 80
    assert (source / copy["avatar"]).read_bytes().startswith(b"\x89PNG")


def test_profile_does_not_count_wrong_nonempty_text_as_success():
    class IgnoringField:
        def fill(self, text):
            pass

        def input_value(self):
            return "old bio"

        def click(self):
            pass

        def type(self, text, delay=None):
            pass

    assert profile._set_text(IgnoringField(), "new bio") is False
