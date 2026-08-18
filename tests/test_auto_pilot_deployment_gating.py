"""Safety-model correction, 2026-08-18: TikTok has no draft-save action — an unpublished,
unclicked upload is discarded by TikTok, not saved anywhere (disproven directly by the
account owner checking TikTok Studio and the mobile app after a real test; an earlier
version of this codebase wrongly assumed the opposite). Phase 5 (Deployment) must therefore
require an explicit publish=True, not just auto_upload=True, or it silently wastes every
upload attempt for nothing."""

import auto_pilot


def test_deploy_requires_both_auto_upload_and_publish():
    assert auto_pilot.should_deploy(auto_upload=True, publish=True, survivors=["x"]) is True


def test_no_deploy_when_auto_upload_without_publish():
    # This is the exact bug this fix closes: auto_upload alone used to trigger a browser
    # upload that TikTok would just discard.
    assert auto_pilot.should_deploy(auto_upload=True, publish=False, survivors=["x"]) is False


def test_no_deploy_when_publish_without_auto_upload():
    assert auto_pilot.should_deploy(auto_upload=False, publish=True, survivors=["x"]) is False


def test_no_deploy_when_neither_set():
    assert auto_pilot.should_deploy(auto_upload=False, publish=False, survivors=["x"]) is False


def test_no_deploy_when_no_survivors_even_if_both_flags_set():
    assert auto_pilot.should_deploy(auto_upload=True, publish=True, survivors=[]) is False
