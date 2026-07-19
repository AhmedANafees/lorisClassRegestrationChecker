"""
Shared LORIS browser-automation helpers, used by both phase1 (single
ad-hoc course check) and phase2 (batch check driven by courses.json).

Keeping this in one place means phase2 can reuse the exact same
term-select / subject-select / search logic inside ONE continuous
browser session for a whole batch of courses, instead of relaunching
the browser and logging in fresh for every single course.
"""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

SESSION_FILE = Path(__file__).parent / "session.json"
LOG_FILE = Path(__file__).parent / "loris_checker.log"
REGISTRATION_URL = "https://loris.wlu.ca/register/ssb/registration"
TERM_SELECT_URL = "https://loris.wlu.ca/register/ssb/term/termSelection?mode=registration"
# Found via <meta name="logoutEndpoint" content="saml/logout"> in the page.
# Banner enforces one active registration session per account -- if a
# script run doesn't call this before exiting, the *next* run gets
# blocked with a "Multiple sessions open" error. Always logout on exit.
LOGOUT_URL = "https://loris.wlu.ca/register/saml/logout"


def setup_logging():
    """Configure logging to BOTH the console and a rotating log file
    (loris_checker.log, capped at 2MB x 3 backups so it doesn't grow
    forever). Call this once at the start of any long-running script.
    Safe to call more than once -- won't duplicate handlers."""
    logger = logging.getLogger()
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def dump_debug(page, label="debug"):
    png_path = Path(__file__).parent / f"{label}.png"
    html_path = Path(__file__).parent / f"{label}.html"
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Exception as e:
        logging.warning(f"Couldn't save screenshot: {e}")
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as e:
        logging.warning(f"Couldn't save page content: {e}")
    logging.info(f"Saved {png_path.name} and {html_path.name} for debugging.")


def logout(page):
    """Full SAML logout -- ends the ENTIRE SSO session, not just the
    registration module. This invalidates session.json, meaning the next
    run needs a fresh phase0_login.py (password + MFA) to get back in.
    Only use this as a last resort when Banner is already stuck (see
    dismiss_multiple_sessions_dialog) -- NOT as routine end-of-run cleanup."""
    try:
        page.goto(LOGOUT_URL, timeout=10000)
        page.wait_for_load_state("networkidle", timeout=10000)
        logging.info("Logged out cleanly (full SSO session ended).")
    except Exception as e:
        logging.warning(f"Logout may not have completed cleanly: {e}")


def return_to_landing(page):
    """Navigate back to the neutral registration landing page at the end
    of a run, WITHOUT logging out. This is what a normal user does when
    they're done -- it releases the registration module's internal state
    without touching the underlying SSO session, so session.json stays
    valid for the next run (no repeat MFA needed)."""
    try:
        page.goto(REGISTRATION_URL, timeout=10000)
        page.wait_for_load_state("networkidle", timeout=10000)
        logging.info("Back at registration landing page (session preserved).")
    except Exception as e:
        logging.warning(f"Couldn't return to landing page cleanly: {e}")


def save_session(context):
    """Re-save session.json with the browser's CURRENT cookies at the end
    of a run. Banner appears to rotate session cookies during registration
    activity -- if we keep replaying the frozen snapshot from the original
    phase0_login.py forever, each run presents an increasingly stale
    identity, which is likely what was causing 'Multiple sessions open'
    on the second run. Treating session.json as a rolling/live file
    (always reflecting the most recent state) avoids that."""
    try:
        context.storage_state(path=str(SESSION_FILE))
        logging.info("session.json refreshed with current cookies.")
    except Exception as e:
        logging.warning(f"Couldn't refresh session.json: {e}")


def dismiss_multiple_sessions_dialog(page, debug=False):
    """Banner sometimes shows a blocking 'Multiple sessions open' popup
    (leftover from a run that didn't logout cleanly). Detect it, log
    out properly, and report what happened."""
    if page.get_by_text("Multiple sessions open", exact=False).count() > 0:
        logging.warning("Banner says: 'Multiple sessions open.' "
                        "This happens when a previous run didn't log out cleanly. "
                        "Logging out now to clear it.")
        if debug:
            dump_debug(page, "debug_multiple_sessions")
        try:
            page.get_by_role("button", name="Ok").click(timeout=5000)
        except PWTimeout:
            pass
        logout(page)
        return True
    return False


def verify_logged_in(page, debug=False):
    """Navigate to the registration landing page and confirm we're
    actually authenticated (not treated as Guest). Returns True/False."""
    logging.info(f"Opening {REGISTRATION_URL} to verify login status...")
    page.goto(REGISTRATION_URL)
    page.wait_for_load_state("networkidle")

    if page.get_by_text("Guest Sign In").count() > 0:
        logging.error("NOT LOGGED IN -- session.json is expired or invalid. "
                      "LORIS is treating this browser as a Guest. Fix: run "
                      "phase0_login.py again, and run this script shortly "
                      "afterward (Laurier's SSO session may expire after a "
                      "period of inactivity).")
        return False

    logging.info("Logged in confirmed.")
    return True


def get_available_terms(page, debug=False):
    """Open the term-selection widget and return the list of term names
    currently open for registration, WITHOUT selecting one. Used when a
    course in courses.json doesn't specify a term -- we check all of them."""
    page.goto(TERM_SELECT_URL)
    page.wait_for_load_state("networkidle")

    try:
        page.click("#s2id_txt_term .select2-choice", timeout=8000)
        page.wait_for_selector("li.select2-result-selectable", timeout=8000)
        results = page.locator("li.select2-result-selectable")
        terms = [results.nth(i).inner_text() for i in range(results.count())]
        # Close the dropdown without selecting anything
        page.keyboard.press("Escape")
        return terms
    except PWTimeout:
        logging.warning("Couldn't read the list of available terms.")
        if debug:
            dump_debug(page, "debug_available_terms_failed")
        return []


def select_term(page, term_name, debug=False):
    """Handle the 'Select a Term' screen. This is a Select2 v3 widget built
    on a hidden <input id="txt_term">, not a native <select>. Clicking it
    open reveals all available terms as li.select2-result-selectable
    elements with plain text like "Fall 2026" -- no typing/filtering needed.
    Returns True on success, False if the term wasn't found/selectable."""
    page.goto(TERM_SELECT_URL)
    page.wait_for_load_state("networkidle")

    if debug:
        dump_debug(page, "debug_term_page_raw")

    try:
        page.click("#s2id_txt_term .select2-choice", timeout=8000)
        page.wait_for_selector("li.select2-result-selectable", timeout=8000)
        results = page.locator("li.select2-result-selectable")

        if debug:
            logging.info(f"Found {results.count()} term option(s):")
            for i in range(results.count()):
                logging.info(f"  - {results.nth(i).inner_text()}")

        match = results.filter(has_text=term_name)
        if match.count() == 0:
            logging.warning(f"No term option matched '{term_name}'.")
            if debug:
                dump_debug(page, "debug_term_no_match")
            return False

        match.first.click()
        page.get_by_role("button", name="Continue").click()
        page.wait_for_load_state("networkidle")
        return True
    except PWTimeout:
        logging.error("Term selection widget didn't behave as expected.")
        if debug:
            dump_debug(page, "debug_term_select_failed")
        return False


def search_again(page, debug=False):
    """Reset the search form for a new query WITHOUT re-selecting the term
    or navigating away -- used when consecutive courses share the same
    term, so we don't redo the whole term-selection flow each time."""
    try:
        page.get_by_role("button", name="Search Again").click(timeout=5000)
        page.wait_for_load_state("networkidle")
        return True
    except PWTimeout:
        logging.warning("Couldn't click 'Search Again' -- will fall back to a full term re-select.")
        if debug:
            dump_debug(page, "debug_search_again_failed")
        return False


def select_subject(page, subject, debug=False):
    """Subject LOOKS like a plain text box but is actually a select2
    'multi' (tag-style) widget wrapping a hidden <input id="txt_subject">.
    You have to click it open, then TYPE (real keystrokes, not .fill())
    to trigger its AJAX search -- Select2 v3 listens for keyup events,
    so setting the value directly doesn't make it query for matches.

    Note: the page actually has TWO elements with id="s2id_txt_subject"
    (basic search + a hidden advanced-search panel share the same ID,
    which is invalid HTML but that's what LORIS ships). We use .first
    and scope everything to that one container to avoid ambiguity."""
    try:
        subject_container = page.locator("#s2id_txt_subject").first
        subject_container.click(timeout=8000)

        search_input = subject_container.locator(".select2-input")
        search_input.press_sequentially(str(subject), delay=80)

        page.wait_for_selector(
            ".select2-drop-active li.select2-result-selectable, "
            ".select2-drop-active li.select2-no-results",
            timeout=8000,
        )
        results = page.locator(".select2-drop-active li.select2-result-selectable")

        if debug:
            logging.info(f"Found {results.count()} subject match(es) for '{subject}':")
            for i in range(results.count()):
                logging.info(f"  - {results.nth(i).inner_text()}")

        if results.count() == 0:
            logging.warning(f"No subject matched '{subject}'.")
            if debug:
                dump_debug(page, "debug_subject_no_match")
            return False

        results.first.click()
        return True
    except PWTimeout:
        logging.error("Couldn't operate the Subject field.")
        if debug:
            dump_debug(page, "debug_subject_field_failed")
        return False


def parse_seats(status_text):
    """Turn Banner's seat-status text into a structured verdict.

    Real examples seen on the actual page:
      "330 of 375 seats remain.\n38 of 38 waitlist seats remain."
      "55 of 200 seats remain.\n20 of 20 waitlist seats remain.Time Conflict!"
      "FULL"  (when a section has zero seats -- Banner's own label for this)
    """
    seats_match = re.search(r"(\d+)\s+of\s+(\d+)\s+seats remain", status_text)
    waitlist_match = re.search(r"(\d+)\s+of\s+(\d+)\s+waitlist seats remain", status_text)

    if seats_match:
        available, total = int(seats_match.group(1)), int(seats_match.group(2))
        is_open = available > 0
    elif "FULL" in status_text.upper():
        available, total, is_open = 0, None, False
    else:
        available, total, is_open = None, None, None

    waitlist_available = int(waitlist_match.group(1)) if waitlist_match else None
    waitlist_total = int(waitlist_match.group(2)) if waitlist_match else None

    return {
        "is_open": is_open,
        "seats_available": available,
        "seats_total": total,
        "waitlist_available": waitlist_available,
        "waitlist_total": waitlist_total,
        "raw": status_text,
    }


def search_by_course(page, subject, course_number, debug=False):
    """Search for a course by subject + course number (e.g. 'Computer
    Science' / 104) and return structured info for every section found.
    Assumes the term has already been selected (or a prior search's
    'Search Again' has been clicked to reset the form)."""
    if not select_subject(page, subject, debug=debug):
        return []

    try:
        page.get_by_label("Course Number", exact=True).fill(str(course_number))
    except PWTimeout:
        logging.error("Couldn't find the Course Number field.")
        if debug:
            dump_debug(page, "debug_course_input_failed")
        return []

    try:
        # get_by_role(name="Search") also matches the "Advanced Search" link
        # (its accessible name contains "Search" too), so target the real
        # button's stable id instead.
        page.click("#search-go", timeout=8000)
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#searchResultsTable table tbody tr", timeout=10000)
    except PWTimeout:
        logging.warning("No results table appeared after searching.")
        if debug:
            dump_debug(page, "debug_no_course_results")
        return []

    # Scope to #searchResultsTable specifically -- the page also has a
    # weekly schedule/calendar widget AND a "my registered classes" table,
    # both of which are plain <table> elements too.
    rows = page.locator("#searchResultsTable table tbody tr")
    count = rows.count()

    if debug:
        logging.info(f"Found {count} row(s) in the results table.")
        dump_debug(page, "debug_course_results")

    sections = []
    for i in range(count):
        row = rows.nth(i)

        def cell(prop):
            loc = row.locator(f'td[data-property="{prop}"]')
            return loc.inner_text().strip() if loc.count() > 0 else ""

        status_text = cell("status")
        seat_info = parse_seats(status_text)

        sections.append({
            "title": cell("courseTitle"),
            "subject": cell("subjectDescription"),
            "course_number": cell("courseNumber"),
            "section": cell("sequenceNumber"),
            "crn": cell("courseReferenceNumber"),
            "instructor": cell("instructor").replace("(Primary)", "").strip(),
            "campus": cell("campus"),
            **seat_info,
        })

    return sections