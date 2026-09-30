@echo off
REM Fallback if Baseball-Reference blocks GitHub's servers: run the same refresh from this PC.
REM Needs Python 3 (python.org installer, tick "Add to PATH") and Git for Windows.
REM Schedule it with Task Scheduler (daily, 6:30 AM) or just double-click.
cd /d "%~dp0"
git pull --quiet || goto :err
python -m pip install -q -r requirements.txt || goto :err
python scraper\bref_current.py --out docs || goto :err
python build\leaderboards.py --data docs --out wp || goto :err
git add docs wp
git diff --cached --quiet || git commit -m "Local refresh %date%"
git push || goto :err
echo Done. WordPress pages publish on the next GitHub run (Actions ^> Nightly ^> Run workflow, tick skip_scrape).
exit /b 0
:err
echo REFRESH FAILED & pause & exit /b 1
