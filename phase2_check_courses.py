"""
Phase 2 - Check a whole list of courses from courses.json in ONE
continuous browser session (single login, no logout -- see loris_common
for why) instead of
restarting the browser/session for every course.

courses.json format:
    [
      {"subject": "Computer Science", "course": "104"},
      {"subject": "Computer Science", "course": "212", "term": "Fall 2026"}
    ]

"term" is optional. If omitted, the course is checked against EVERY
term currently open for registration (discovered once at the start of
the run). If given, only that term is checked.

Usage:
    python phase2_check_courses.py
    python phase2_check_courses.py --config my_courses.json
    python phase2_check_courses.py --debug
"""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import loris_common as lc
import notifier

DEFAULT_CONFIG = Path(__file__).parent / "courses.json"

# When a course in courses.json doesn't specify a term, only check
# these -- not every term Banner happens to have open (which can
# include terms you don't care about, e.g. a stray Winter 2026).
DEFAULT_TERMS_TO_CHECK = ["Fall 2026", "Winter 2027", "Spring 2027"]


def load_courses(config_path):
    if not config_path.exists():
        print(f"No config file found at {config_path}.")
        print('Create one like: [{"subject": "Computer Science", "course": "104"}]')
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_section_line(term, s):
    if s["is_open"] is True:
        status_line = f"OPEN -- {s['seats_available']} of {s['seats_total']} seats"
    elif s["is_open"] is False and s["seats_total"] is not None:
        status_line = f"FULL -- 0 of {s['seats_total']} seats"
    elif s["is_open"] is False:
        status_line = "FULL"
    else:
        status_line = f"UNKNOWN -- couldn't parse: {s['raw']!r}"

    print(f"  [{term}] {s['subject']} {s['course_number']} Section {s['section']} "
          f"(CRN {s['crn']}) -- {status_line}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                         help="Path to courses.json (default: courses.json in this folder)")
    parser.add_argument("--debug", action="store_true", help="Save screenshots/HTML on failure")
    args = parser.parse_args()

    courses = load_courses(Path(args.config))
    if not courses:
        print("courses.json is empty -- nothing to check.")
        sys.exit(0)

    if not lc.SESSION_FILE.exists():
        print(f"No session.json found at {lc.SESSION_FILE}.")
        print("Run phase0_login.py first.")
        sys.exit(1)

    all_results = []  # list of (course, term, sections) for the final summary

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.debug)
        page = None
        try:
            context = browser.new_context(storage_state=str(lc.SESSION_FILE))
            page = context.new_page()

            if not lc.verify_logged_in(page, debug=args.debug):
                sys.exit(1)

            # Only bother discovering available terms if at least one
            # course in the config doesn't specify one.
            available_terms = []
            if any("term" not in c for c in courses):
                print("\nSome courses don't specify a term -- checking which terms are open...")
                all_open_terms = lc.get_available_terms(page, debug=args.debug)
                if not all_open_terms:
                    print("Couldn't determine available terms. Aborting.")
                    sys.exit(1)
                print(f"Open terms on LORIS: {', '.join(all_open_terms)}")

                # Only keep the ones we actually care about
                available_terms = [t for t in DEFAULT_TERMS_TO_CHECK if t in all_open_terms]
                missing = [t for t in DEFAULT_TERMS_TO_CHECK if t not in all_open_terms]
                if missing:
                    print(f"Note: {', '.join(missing)} not currently open for "
                          f"registration -- skipping.")
                print(f"Will check: {', '.join(available_terms)}")

            current_term = None  # tracks what term the browser is currently on

            for course in courses:
                subject = course["subject"]
                course_number = course["course"]
                terms_to_check = [course["term"]] if "term" in course else available_terms

                for term in terms_to_check:
                    print(f"\nChecking {subject} {course_number} for {term}...")

                    if term != current_term:
                        # Term changed (or this is the first search) --
                        # full term-selection navigation required.
                        if not lc.select_term(page, term, debug=args.debug):
                            print(f"Skipping {subject} {course_number} ({term}) -- "
                                  f"couldn't select that term.")
                            continue
                        current_term = term
                    else:
                        # Same term as the previous search -- just reset
                        # the search form instead of redoing term select.
                        if not lc.search_again(page, debug=args.debug):
                            # Fallback: force a full re-select of the same term
                            if not lc.select_term(page, term, debug=args.debug):
                                print(f"Skipping {subject} {course_number} ({term}) -- "
                                      f"couldn't reset the search form.")
                                continue

                    if lc.dismiss_multiple_sessions_dialog(page, debug=args.debug):
                        sys.exit(1)

                    sections = lc.search_by_course(page, subject, course_number, debug=args.debug)
                    all_results.append((f"{subject} {course_number}", term, sections))

                    if sections:
                        for s in sections:
                            print_section_line(term, s)
                    else:
                        print(f"  No sections found for {subject} {course_number} in {term}.")

            # Final summary
            print("\n" + "=" * 60)
            print("SUMMARY")
            print("=" * 60)
            any_open = False
            summary_lines = []
            for course_label, term, sections in all_results:
                open_sections = [s for s in sections if s["is_open"]]
                if open_sections:
                    any_open = True
                    crns = ", ".join(f"CRN {s['crn']} ({s['seats_available']} seats)" for s in open_sections)
                    line = f"OPEN: {course_label} [{term}] -- {crns}"
                    print(line)
                    summary_lines.append(line)
            if not any_open:
                print("No open seats found in any tracked course/term right now.")

            if any_open:
                notifier.send_email(
                    subject=f"LORIS: {len(summary_lines)} course(s) have open seats!",
                    body="\n".join(summary_lines),
                )

        finally:
            if page is not None:
                lc.return_to_landing(page)
                lc.save_session(context)
            browser.close()


if __name__ == "__main__":
    main()