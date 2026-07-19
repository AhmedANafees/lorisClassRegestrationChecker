# LORIS Course Watcher — Phase 0

This has to run on **your own computer**

## One-time setup

```bash
cd loris-bot
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 -m playwright install chromium
```

## Run Phase 0

```bash
python3 phase0_login.py
```

A browser window will pop up on your screen. Log in exactly as you
normally would (password, then approve the MFA push on your phone).
Once you see the LORIS dashboard, go back to the terminal and press
Enter. It will save `session.json` next to the script.

**Important:** `session.json` = your live login. Don't share it, don't
commit it to GitHub. Add it to a `.gitignore` if you use git.

## What's next

Once you've run this and have a `session.json`, tell me and we'll
build Phase 1: reusing that session to check if a specific course has
open seats.
