import pytest
import tiktok_uploader


@pytest.mark.parametrize('label', ['Post now', 'Jetzt veröffentlichen'])
def test_publish_confirmation_accepts_observed_english_and_german_labels(label):
    clicked = []

    class Button:
        def click(self, timeout):
            clicked.append(label)

    class Page:
        def get_by_role(self, role, name):
            assert role == 'button'
            assert name.fullmatch(label)
            assert not name.fullmatch('Cancel')
            assert not name.fullmatch('Discard')
            return Button()

    assert tiktok_uploader._confirm_publish_despite_pending_review(Page())
    assert clicked == [label]
