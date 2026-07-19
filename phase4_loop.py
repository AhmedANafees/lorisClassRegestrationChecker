"""
Phase 4 - Run Phase 2's course check on a loop, and only send an email
when a section NEWLY opens (goes from not-open -> open) -- not on
every single poll once it's already known to be open.

IMPORTANT DESIGN NOTE: this keeps ONE browser window open for the
ENTIRE run, reusing the same page for every check cycle -- it does NOT
relaunch the browser each time. Earlier versions opened a fresh browser
(with the same cookies) for every single check, which appears to be
exactly what caused Banner's "Multiple sessions open" error: repeatedly
closing and reopening a "session" in quick succession looks like
overlapping/duplicate sessions to the server, even with identical
cookies. Behaving like a real person who just leaves one LORIS tab
open and periodically re-searches avoids that entirely.

State (what was open last time) is saved to state.json so restarting
the script doesn't cause a fresh round of "it's open!" emails for
sections you already know about.

Usage:
    python phase4_loop.py
    python phase4_loop.py --interval 300          (check every 5 min, default)
    python phase4_loop.py --config my_courses.json

Stop with Ctrl+C at any time -- it finishes the current check cleanly
before exiting.

At any point while it's sleeping between checks, press Enter or type
'logout' (then Enter) to immediately end the session. This is a full
SAML logout, so you'll need to run phase0_login.py again afterward to
resume checking -- use this when you're stepping away and want to be
sure the session is closed, not as routine behavior.

IMPORTANT LIMITATION: LORIS's login session eventually expires (SSO
session timeout), and MFA can't be automated around -- that's the
whole point of MFA. When that happens, this script CANNOT log back in
by itself. It will detect this, send you one "please log back in"
email (not one per poll), close the (now-useless) browser, and keep
retrying every interval -- relaunching fresh each retry only while
logged out, since there's no "session" to protect at that point.
"""

import argparse
import json
import logging
import queue
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

import loris_common as lc
import notifier
from phase2_check_courses import (
    DEFAULT_CONFIG,
    DEFAULT_TERMS_TO_CHECK,
    load_courses,
    print_section_line,
)

STATE_FILE = Path(__file__).parent / "state.json"


def stdin_listener(input_queue):
    """Runs in a background thread, reading terminal input line-by-line
    without blocking the main check/sleep loop. Used so you can press
    Enter or type 'logout' at any time to force an immediate logout,
    without waiting for the current sleep interval to finish."""
    while True:
        try:
            line = input()
        except EOFError:
            break
        input_queue.put(line)


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"sections": {}, "login_alert_sent": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def open_browser_session(debug=False):
    """Launch a browser and log in ONCE. Returns (playwright, browser,
    context, page) on success, or None if login failed. Caller is
    responsible for keeping these alive across check cycles and
    eventually closing them."""
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=not debug)
    context = browser.new_context(storage_state=str(lc.SESSION_FILE))
    page = context.new_page()

    if not lc.verify_logged_in(page, debug=debug):
        context.close()
        browser.close()
        p.stop()
        return None

    return p, browser, context, page


def close_browser_session(session, do_logout=False):
    """Cleanly shut down a browser session opened by open_browser_session.
    do_logout=True performs a full SAML logout (only used for the
    manual 'logout' command or when the whole script is exiting for
    good); otherwise just returns to the landing page and refreshes
    session.json, preserving the session for next time."""
    if session is None:
        return
    p, browser, context, page = session
    try:
        if do_logout:
            lc.logout(page)
        else:
            lc.return_to_landing(page)
            lc.save_session(context)
    except Exception as e:
        logging.warning(f"Error while closing browser session: {e}")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


def run_one_check(page, courses, debug=False):
    """Run a single check pass using an ALREADY-OPEN, already-logged-in
    page. Returns a dict of {section_key: section_info}, or None if
    something during the check revealed we're no longer logged in
    (e.g. Banner's own session lock/timeout) -- in which case the
    caller should close this browser session and open a fresh one.

    A failure on any ONE course/term (site hiccup, unexpected timeout)
    is logged and skipped rather than aborting the whole batch."""
    results = {}

    available_terms = []
    if any("term" not in c for c in courses):
        all_open_terms = lc.get_available_terms(page, debug=debug)
        available_terms = [t for t in DEFAULT_TERMS_TO_CHECK if t in all_open_terms]

    current_term = None
    for course in courses:
        subject = course["subject"]
        course_number = course["course"]
        terms_to_check = [course["term"]] if "term" in course else available_terms

        for term in terms_to_check:
            try:
                if term != current_term:
                    if not lc.select_term(page, term, debug=debug):
                        logging.warning(f"Skipping {subject} {course_number} "
                                         f"({term}) -- couldn't select term.")
                        continue
                    current_term = term
                else:
                    if not lc.search_again(page, debug=debug):
                        if not lc.select_term(page, term, debug=debug):
                            logging.warning(f"Skipping {subject} {course_number} "
                                             f"({term}) -- couldn't reset search form.")
                            continue

                if lc.dismiss_multiple_sessions_dialog(page, debug=debug):
                    return None

                sections = lc.search_by_course(page, subject, course_number, debug=debug)
                for s in sections:
                    key = f"{term}|{s['crn']}"
                    results[key] = {
                        "term": term,
                        "label": f"{subject} {course_number}",
                        **s,
                    }
            except Exception as e:
                logging.error(f"Unexpected error checking {subject} "
                              f"{course_number} ({term}): {e}")
                current_term = None  # force a clean re-select next time, state is uncertain
                continue

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--interval", type=int, default=300,
                         help="Seconds between checks (default: 300 = 5 min)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    lc.setup_logging()

    config_path = Path(args.config)
    state = load_state()

    input_queue = queue.Queue()
    listener = threading.Thread(target=stdin_listener, args=(input_queue,), daemon=True)
    listener.start()

    logging.info(f"Starting poll loop. Checking every {args.interval} seconds. Ctrl+C to stop.")
    logging.info("At any time, press Enter or type 'logout' to immediately log out.")
    logging.info("Keeping one browser session open for the whole run "
                 "(not relaunching per check) to avoid Banner's session lock.")

    session = None  # (playwright, browser, context, page) tuple, or None if logged out

    try:
        while True:
            logging.info("Checking...")

            if session is None:
                logging.info("Opening a browser session...")
                session = open_browser_session(debug=args.debug)

            results = None
            if session is not None:
                _, _, _, page = session
                try:
                    courses = load_courses(config_path)
                    results = run_one_check(page, courses, debug=args.debug)
                except Exception as e:
                    logging.error(f"Check failed unexpectedly: {e}")
                    results = None

                if results is None:
                    # Something's wrong with this session (logged out mid-run,
                    # Banner's own lock, or a crash) -- close it so we open a
                    # completely fresh one next time instead of continuing to
                    # reuse a possibly-broken page.
                    close_browser_session(session)
                    session = None

            if results is None:
                logging.warning("Login failed or session expired -- can't check right now.")
                if not state.get("login_alert_sent"):
                    notifier.send_email(
                        subject="LORIS checker: login needed",
                        body="Your LORIS session has expired and the checker can't "
                             "log back in automatically (MFA can't be automated). "
                             "Please run phase0_login.py again. The checker will "
                             "keep retrying every interval in the meantime.",
                    )
                    state["login_alert_sent"] = True
                    save_state(state)
            else:
                state["login_alert_sent"] = False  # we're logged in fine now

                newly_opened = []
                for key, info in results.items():
                    was_open = state["sections"].get(key, {}).get("is_open", False)
                    if info["is_open"] and not was_open:
                        newly_opened.append(info)
                    state["sections"][key] = {"is_open": info["is_open"]}
                    print_section_line(info["term"], info)

                save_state(state)

                if newly_opened:
                    logging.info(f"{len(newly_opened)} section(s) newly opened!")
                    lines = [
                        f"{s['label']} [{s['term']}] Section {s['section']} "
                        f"(CRN {s['crn']}) -- {s['seats_available']} of {s['seats_total']} seats"
                        for s in newly_opened
                    ]
                    notifier.send_email(
                        subject=f"LORIS: {len(newly_opened)} section(s) just opened!",
                        body="\n".join(lines),
                    )
                else:
                    logging.info("No new openings this check.")

            logging.info(f"Sleeping {args.interval} seconds... "
                         f"(press Enter or type 'logout' to log out now)")
            try:
                line = input_queue.get(timeout=args.interval)
                if line.strip().lower() in ("", "logout", "log out"):
                    logging.info("Manual logout requested...")
                    close_browser_session(session, do_logout=True)
                    session = None
                    logging.info("Logged out. Run phase0_login.py when ready to resume.")
                else:
                    logging.info(f"Ignoring input: {line!r} (press Enter or type 'logout')")
            except queue.Empty:
                pass  # normal timeout -- just means it's time for the next check
            except KeyboardInterrupt:
                logging.info("Stopped.")
                break
    finally:
        # Always leave things tidy on exit -- preserve the session
        # (no forced logout) unless the user already asked for one above.
        close_browser_session(session)


if __name__ == "__main__":
    main()