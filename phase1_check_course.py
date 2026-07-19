"""
Phase 1 - Quick one-off check: search a single course by subject +
course number and print seat status for every section found.

For checking a whole list of courses at once, use phase2_check_courses.py
with a courses.json config instead -- this script is for a quick manual
check of one course.

Usage:
    python phase1_check_course.py --term "Fall 2026" --subject "Computer Science" --course 104
    python phase1_check_course.py --term "Fall 2026" --subject "Computer Science" --course 104 --debug

Requires session.json from phase0_login.py to already exist.
"""

import argparse
import sys

from playwright.sync_api import sync_playwright

import loris_common as lc


def print_results(sections):
    if not sections:
        print("\nNo results captured. If you ran with --debug, check the debug_*.png / .html files.")
        return

    print(f"\n--- Found {len(sections)} section(s) ---\n")
    for s in sections:
        if s["is_open"] is True:
            status_line = f"OPEN -- {s['seats_available']} of {s['seats_total']} seats"
        elif s["is_open"] is False and s["seats_total"] is not None:
            status_line = f"FULL -- 0 of {s['seats_total']} seats"
        elif s["is_open"] is False:
            status_line = "FULL"
        else:
            status_line = f"UNKNOWN -- couldn't parse: {s['raw']!r}"

        print(f"{s['subject']} {s['course_number']} Section {s['section']} "
              f"(CRN {s['crn']}) -- {status_line}")
        print(f"  Instructor: {s['instructor']}  |  Campus: {s['campus']}")
        if s["waitlist_available"] is not None:
            print(f"  Waitlist: {s['waitlist_available']} of {s['waitlist_total']} seats")
        print()

    open_sections = [s for s in sections if s["is_open"]]
    if open_sections:
        print(f"=> {len(open_sections)} section(s) currently have open seats!")
    else:
        print("=> No open seats right now.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--term", required=True,
                         help='Term name exactly as shown in LORIS, e.g. "Fall 2026"')
    parser.add_argument("--subject", required=True,
                         help='Subject as shown in LORIS, e.g. "Computer Science" or "CP"')
    parser.add_argument("--course", required=True,
                         help='Course number, e.g. "104"')
    parser.add_argument("--debug", action="store_true", help="Save screenshots/HTML on failure")
    args = parser.parse_args()

    if not lc.SESSION_FILE.exists():
        print(f"No session.json found at {lc.SESSION_FILE}.")
        print("Run phase0_login.py first.")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.debug)
        page = None
        try:
            context = browser.new_context(storage_state=str(lc.SESSION_FILE))
            page = context.new_page()

            if not lc.verify_logged_in(page, debug=args.debug):
                sys.exit(1)

            lc.select_term(page, args.term, debug=args.debug)

            if lc.dismiss_multiple_sessions_dialog(page, debug=args.debug):
                sys.exit(1)

            sections = lc.search_by_course(page, args.subject, args.course, debug=args.debug)
            print_results(sections)
        finally:
            if page is not None:
                lc.return_to_landing(page)
                lc.save_session(context)
            browser.close()


if __name__ == "__main__":
    main()