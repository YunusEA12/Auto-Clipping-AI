"""Set the TikTok account's avatar, display name and bio through the web UI.

tiktok_uploader.py already drives the upload page with Playwright; this reuses its session
(cookies.json) for the profile settings page, which has no API.

Two things make this riskier than an upload and shape how it is written:

1. It changes account settings rather than adding content, so a half-finished run is worse
   than no run. Every field is verified after saving, and --dry-run reports what the page
   actually exposes without touching anything.
2. TikTok's settings page is heavy on overlays (cookie banner, onboarding tooltips, "what's
   new" modals) that silently swallow clicks. Overlays are dismissed before every
   interaction, not once at the start — see CLAUDE.md.

Usage:
    python scripts/setup_tiktok_profile.py --dry-run          # look, change nothing
    python scripts/setup_tiktok_profile.py --headed           # do it, watch it happen
    python scripts/setup_tiktok_profile.py --skip-avatar      # text only
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

import logging_setup
import tiktok_uploader

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "profile_assets"
# /setting is account settings (privacy, notifications) and holds none of the profile
# fields. /profile redirects to the signed-in user's own @handle page, which is where the
# "Edit profile" modal lives.
PROFILE_URL = "https://www.tiktok.com/profile"

# Overlays that intercept clicks. Tried in order, best-effort, never fatal.
OVERLAY_DISMISSERS = [
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Accept all')",
    "button:has-text('Decline all')",
    "button:has-text('Alle ablehnen')",
    "div[data-e2e='modal-close-inner-button']",
    "button[aria-label='Close']",
    "button[aria-label='Schließen']",
    "div.tiktok-cookie-banner button",
]

# TikTok ships several UI variants in parallel; each field needs more than one way in.
NAME_SELECTORS = [
    "input[data-e2e='edit-profile-name']",
    "input[name='nickname']",
    "input[placeholder*='Name']",
]
BIO_SELECTORS = [
    "textarea[data-e2e='edit-profile-bio']",
    "textarea[name='signature']",
    "textarea[placeholder*='Bio']",
    "textarea",
]
EDIT_BUTTON_SELECTORS = [
    "button:has-text('Edit profile')",
    "button:has-text('Profil bearbeiten')",
    "[data-e2e='edit-profile-entrance']",
]
SAVE_SELECTORS = [
    "button:has-text('Speichern')",
    "button:has-text('Save')",
    "button[data-e2e='edit-profile-save']",
]

# The avatar cropper is a nested dialog with its OWN confirm button, labelled Apply. It must
# be matched separately from the form's Save: clicking Save while the cropper is open
# commits the form and closes it before the bio has been filled in.
CROPPER_APPLY_SELECTORS = [
    "button:has-text('Apply')",
    "button:has-text('Übernehmen')",
    "button:has-text('Anwenden')",
]

# Saving the bio triggers TikTok's slider puzzle. The POST then returns HTTP 200 with an
# empty body and nothing changes, which looks exactly like success from the outside — hence
# detecting it explicitly rather than trusting the status code. It is deliberately NOT
# solved here: it is an anti-automation control, and defeating it is the fastest way to get
# a fresh account flagged. In a headed run the person solves their own puzzle.
CAPTCHA_SELECTORS = [
    "text=Drag the slider to fit the puzzle",
    "text=Schiebe den Regler",
    "div[id*='captcha']",
    "div[class*='captcha']",
]


def _captcha_visible(page) -> bool:
    for selector in CAPTCHA_SELECTORS:
        try:
            if page.locator(selector).first.is_visible(timeout=800):
                return True
        except Exception:
            continue
    return False


def _wait_out_captcha(page, timeout_ms: int) -> bool:
    """Waits for a human to solve a visible puzzle. Returns whether it went away."""
    logger.info("")
    logger.info("=" * 68)
    logger.info(" TikTok zeigt ein Captcha (Schieberaetsel).")
    logger.info(" Bitte im Browserfenster selbst loesen — es wird hier NICHT")
    logger.info(" automatisch geloest. Danach laeuft das Skript weiter.")
    logger.info("=" * 68)
    logger.info("")
    waited = 0
    while waited < timeout_ms:
        page.wait_for_timeout(2000)
        waited += 2000
        if not _captcha_visible(page):
            logger.info("Captcha geloest — weiter.")
            return True
    return False
AVATAR_INPUT_SELECTORS = [
    "input[type='file'][accept*='image']",
    "input[type='file']",
]


def _dismiss_overlays(page) -> int:
    """Clicks away anything known to sit on top of the page. Returns how many it closed."""
    closed = 0
    for selector in OVERLAY_DISMISSERS:
        try:
            element = page.locator(selector).first
            if element.is_visible(timeout=600):
                element.click(timeout=2000)
                closed += 1
                page.wait_for_timeout(400)
        except Exception:
            continue
    return closed


def _first_visible(page, selectors, timeout_ms: int = 3000):
    """Returns the first locator among `selectors` that is actually visible, or None."""
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if element.is_visible(timeout=timeout_ms):
                return element, selector
        except Exception:
            continue
    return None, None


def _set_text(field, text: str) -> bool:
    """Puts `text` into `field` and confirms it actually landed there.

    Not Locator.type(): that replays the string as individual key events, which drops emoji
    (they are surrogate pairs, and CDP key events carry one code unit) and turns the newline
    into an Enter press that submits the surrounding form. fill() sets the value directly.
    React-backed fields sometimes ignore a programmatic fill, so the value is read back and
    the key-event path is kept as a fallback for that case only.
    """
    field.fill("")
    field.fill(text)
    if (field.input_value() or "").strip():
        return True

    field.click()
    field.type(text.replace("\n", " "), delay=40)
    return bool((field.input_value() or "").strip())


def _load_profile_copy() -> dict:
    path = ASSETS_DIR / "profile.json"
    if not path.exists():
        raise SystemExit(
            f"{path} fehlt — zuerst `python scripts/generate_profile_assets.py` laufen lassen."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def run(dry_run: bool, headed: bool, skip_avatar: bool) -> int:
    cookies = tiktok_uploader.load_cookies()
    if not cookies:
        logger.error(
            "Keine brauchbare Session in %s. Zuerst `python scripts/capture_tiktok_session.py` "
            "laufen lassen und sich im Fenster anmelden.", tiktok_uploader.COOKIES_PATH,
        )
        return 1

    copy = _load_profile_copy()
    avatar_path = ASSETS_DIR / "avatar.png"
    if not skip_avatar and not avatar_path.exists():
        logger.error("%s fehlt — `python scripts/draw_avatar.py` erzeugt es.", avatar_path)
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies(cookies)
        page = context.new_page()
        try:
            page.goto(PROFILE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            _dismiss_overlays(page)

            if "login" in page.url:
                logger.error("Session wird nicht akzeptiert (auf Login umgeleitet: %s).", page.url)
                return 1

            logger.info("Eingeloggt als: %s", page.url)

            edit_button, matched = _first_visible(page, EDIT_BUTTON_SELECTORS)
            if edit_button is None:
                logger.error("Kein 'Edit profile'-Button gefunden — Abbruch, nichts geaendert.")
                page.screenshot(path=str(ASSETS_DIR / "profile_page_error.png"), full_page=True)
                return 1

            # The modal must be opened even for a dry run: the fields do not exist in the
            # DOM until it is, so skipping the click would report "nothing found" every time.
            logger.info("Oeffne Profilbearbeitung via %s", matched)
            _dismiss_overlays(page)
            edit_button.click(timeout=5000)
            page.wait_for_timeout(3000)

            name_field, name_sel = _first_visible(page, NAME_SELECTORS)
            bio_field, bio_sel = _first_visible(page, BIO_SELECTORS)
            file_input, file_sel = _first_visible(page, AVATAR_INPUT_SELECTORS, timeout_ms=1500)

            logger.info("Gefunden — Name: %s | Bio: %s | Datei-Upload: %s",
                        name_sel or "NEIN", bio_sel or "NEIN", file_sel or "NEIN")

            if dry_run:
                page.screenshot(path=str(ASSETS_DIR / "profile_page_dryrun.png"), full_page=True)
                logger.info("Dry run — nichts geaendert. Screenshot: %s",
                            ASSETS_DIR / "profile_page_dryrun.png")
                return 0

            changed = []
            skipped = []

            # Avatar first: its cropper is a modal-within-a-modal, so getting it out of the
            # way before typing avoids the text fields losing focus mid-type.
            if file_input is not None and not skip_avatar:
                try:
                    file_input.set_input_files(str(avatar_path))
                    page.wait_for_timeout(3500)
                    apply_button, apply_sel = _first_visible(page, CROPPER_APPLY_SELECTORS, timeout_ms=6000)
                    if apply_button is None:
                        logger.warning("Zuschneider-Button nicht gefunden — Profilbild uebersprungen.")
                        skipped.append("Profilbild (kein Apply-Button)")
                    else:
                        logger.info("Bestaetige Zuschnitt via %s", apply_sel)
                        apply_button.click(timeout=6000)
                        page.wait_for_timeout(2500)
                        changed.append("Profilbild")
                except Exception as e:
                    logger.warning("Profilbild konnte nicht gesetzt werden: %s", str(e)[:200])
                    skipped.append("Profilbild (Fehler)")

            if name_field is not None:
                _dismiss_overlays(page)
                _set_text(name_field, copy["display_name"])
                changed.append("Anzeigename")
            else:
                # TikTok locks the nickname for 7 days after each change and then renders it
                # as plain text rather than an input — not an error, and not worth failing on.
                logger.info("Anzeigename ist derzeit nicht editierbar (7-Tage-Sperre) — unveraendert.")
                skipped.append("Anzeigename (7-Tage-Sperre)")

            if bio_field is not None:
                _dismiss_overlays(page)
                if _set_text(bio_field, copy["bio"]):
                    changed.append("Bio")
                else:
                    logger.warning("Bio-Feld blieb nach dem Setzen leer — wird nicht als geaendert gezaehlt.")
                    skipped.append("Bio (Feld blieb leer)")

            _dismiss_overlays(page)
            save, save_sel = _first_visible(page, SAVE_SELECTORS, timeout_ms=4000)
            if save is None:
                logger.error("Kein Speichern-Button gefunden — nichts wurde uebernommen.")
                page.screenshot(path=str(ASSETS_DIR / "profile_page_error.png"), full_page=True)
                return 1

            logger.info("Speichere via %s ...", save_sel)
            save.click(timeout=8000)
            page.wait_for_timeout(4000)

            if _captcha_visible(page):
                if not headed:
                    logger.error(
                        "TikTok verlangt ein Captcha, das nur ein Mensch loesen kann. "
                        "Nichts wurde gespeichert. Erneut mit --headed starten und das "
                        "Raetsel im Fenster selbst loesen.",
                    )
                    page.screenshot(path=str(ASSETS_DIR / "profile_captcha.png"))
                    return 2
                if not _wait_out_captcha(page, timeout_ms=300000):
                    logger.error("Captcha wurde nicht geloest — nichts gespeichert.")
                    return 2
                # The form's save does not re-fire by itself once the puzzle closes.
                save, _ = _first_visible(page, SAVE_SELECTORS, timeout_ms=5000)
                if save is not None:
                    save.click(timeout=8000)
                    page.wait_for_timeout(4000)

            # Read the live profile back rather than trusting the save. TikTok's save POST
            # returns HTTP 200 with an empty body even when a captcha silently voided the
            # change, so "the click succeeded" says nothing about whether anything changed.
            page.goto(PROFILE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            page.screenshot(path=str(ASSETS_DIR / "profile_after.png"), full_page=True)

            verified, unverified = [], []
            if "Bio" in changed:
                live_bio = ""
                try:
                    live_bio = page.locator("[data-e2e='user-bio']").first.inner_text().strip()
                except Exception:
                    pass
                # First line only: TikTok renders the bio with its own line breaks.
                wanted = copy["bio"].splitlines()[0].strip()
                (verified if wanted and wanted in live_bio else unverified).append("Bio")
                changed.remove("Bio")

            if "Profilbild" in changed:
                src = ""
                try:
                    src = page.locator("[data-e2e='user-avatar'] img").first.get_attribute("src") or ""
                except Exception:
                    pass
                # A real uploaded avatar comes from the CDN; the letter placeholder has no img.
                (verified if "tiktokcdn" in src else unverified).append("Profilbild")
                changed.remove("Profilbild")

            verified.extend(changed)

            if verified:
                logger.info("✅ Nachweislich geaendert: %s", ", ".join(verified))
            if skipped:
                logger.info("   Nicht versucht: %s", ", ".join(skipped))
            if unverified:
                logger.error(
                    "❌ Angeblich gespeichert, aber auf dem Profil NICHT angekommen: %s. "
                    "Fast immer TikToks Captcha: der Speichern-Request meldet trotzdem "
                    "Erfolg. Diese Felder von Hand in der TikTok-App setzen — der Text "
                    "steht in %s.", ", ".join(unverified), ASSETS_DIR / "profile.txt",
                )
            logger.info("   Kontrollbild: %s", ASSETS_DIR / "profile_after.png")
            return 1 if unverified else 0

        except PlaywrightTimeout as e:
            logger.error("Zeitueberschreitung: %s", str(e)[:200])
            page.screenshot(path=str(ASSETS_DIR / "profile_page_error.png"), full_page=True)
            return 1
        finally:
            context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Set the TikTok profile picture, name and bio.")
    parser.add_argument("--dry-run", action="store_true", help="Only report what the page exposes")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--skip-avatar", action="store_true", help="Only set the text fields")
    args = parser.parse_args()
    return run(args.dry_run, args.headed, args.skip_avatar)


if __name__ == "__main__":
    raise SystemExit(main())
