"""
Phase 0 - One-time manual login to LORIS.

What this does:
1. Opens a REAL, visible Chrome window pointed at LORIS.
2. YOU log in manually (username, password, and approve the MFA push
   on your phone yourself). The script does not touch your password.
3. Once you land on the logged-in homepage, press Enter in the terminal.
4. The script saves your browser session (cookies + local storage) to
   session.json in this folder.

Every later phase reuses session.json instead of logging in again, so
you only have to do this by hand once every so often (until the
session expires, at which point you just re-run this script).

session.json contains live login cookies for your Laurier account.
Treat it like a password: don't commit it to git, don't share it.
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

SESSION_FILE = Path(__file__).parent / "session.json"
LORIS_LOGIN_URL = "https://loris.wlu.ca/register/ssb/personaSelection/selectPersona"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print(f"Opening {LORIS_LOGIN_URL} ...")
        page.goto(LORIS_LOGIN_URL)

        print()
        print("=" * 60)
        print("A browser window has opened.")
        print("1. Log in with your Laurier username and password.")
        print("2. Approve the MFA prompt on your phone.")
        print("3. Wait until you see the LORIS/Banner dashboard.")
        print("4. Come back here and press Enter.")
        print("=" * 60)
        input("Press Enter once you're fully logged in... ")

        if page.get_by_text("Guest Sign In").count() > 0:
            print("\nWARNING: This page still shows 'Guest Sign In' in the top-right.")
            print("It looks like you're not actually logged in yet.")
            print("Make sure you can see your name (not 'Guest Sign In') in the")
            print("top-right corner before continuing.")
            proceed = input("Save the session anyway? (y/N): ").strip().lower()
            if proceed != "y":
                print("Not saving. Log in fully, then run this script again.")
                browser.close()
                return

        context.storage_state(path=str(SESSION_FILE))
        print(f"\nSaved session to {SESSION_FILE}")
        print("You can close the browser window now.")

        browser.close()


if __name__ == "__main__":
    main()