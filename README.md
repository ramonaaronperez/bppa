# BPPA — baseball production website

Nightly pipeline that keeps **baseballproduction.wordpress.com** and the public **BPPA Explorer** current.
No PC, Chrome, or local server involved.

```
GitHub Actions (6:17 AM ET, Mar–Nov)
  scraper/bref_current.py     3 Baseball-Reference pages -> docs/bppa_current_{bat,pit,pitstd}.csv
  build/leaderboards.py       parquet + live CSVs        -> wp/*.html (static tables, free-plan safe)
  tests/test_all.py           parser + anchors (Ruth 1921, Bonds 2001, NYY 1927, Aaron) — fail = nothing published
  build/make_public_explorer  explorer/bppa_lite.html    -> docs/index.html (Fixes tab removed)
  git push                    -> GitHub Pages serves docs/ (the explorer)
  build/publish_wp.py         wp/*.html                  -> WordPress.com pages via REST API
```
A failed run emails you (GitHub's default for failed scheduled workflows) — no more silent staleness.

## One-time setup (about 20 minutes)

**1. GitHub**
1. Create a free account at github.com (pick the username you want in the explorer URL).
2. Install GitHub Desktop → *File → Add local repository* → choose this folder → *Publish repository*.
   Name it `bppa`, **untick "Keep this code private"** (Pages is free only for public repos).
3. On github.com, open the repo → *Settings → Pages* → Source: *Deploy from a branch*, Branch `main`, folder `/docs` → Save.
   Explorer goes live at `https://<username>.github.io/bppa/` within a minute or two.
4. *Settings → Actions → General* → Workflow permissions: **Read and write** → Save.

**2. WordPress.com API access**
1. https://developer.wordpress.com/apps/ → *Create New Application*.
   Name `BPPA publisher`, Website and Redirect URL `https://<username>.github.io/bppa/` (WordPress.com rejects URLs containing "wordpress"; the redirect is never used — `http://localhost` also works), Type **Native**. Note the **Client ID** and **Client Secret**.
2. https://wordpress.com/me/security → **Two-Step Authentication** → turn it on (the *Application passwords* section only appears once 2FA is active) → scroll to *Application passwords* → *Add new application password*, name it `bppa`, *Generate Password*. Copy it.
3. In the GitHub repo: *Settings → Secrets and variables → Actions → New repository secret*, four of them:
   `WPCOM_CLIENT_ID`, `WPCOM_CLIENT_SECRET`, `WPCOM_USER` (your WordPress.com username), `WPCOM_APP_PASSWORD`.

**3. Switch the site over** (Actions tab → *WordPress one-time setup* → Run workflow)
1. `dry-run` — read the log; it lists what would be created and which old pages would be drafted.
2. `publish-and-set-home` — creates the 6 BPPA pages and makes **BPPA** the home page.
3. `retire-old-pages` — moves the ~330 old PPA pages to **Draft** (not deleted; `restore-old-pages` undoes it).
4. In WordPress: *Appearance → Menus* (or *Editor → Navigation*) — replace the old menu with:
   Home (BPPA) · Batting Leaders · Pitching Leaders · Team Leaders · Year by Year · About · **Explorer** (custom link to the GitHub Pages URL).

**4. Nightly** — Actions tab → *Nightly BPPA refresh* → *Run workflow* once to confirm. After that it runs itself.

## If Baseball-Reference blocks GitHub's servers
The scrape step will fail with HTTP 403/429. Run `run_refresh_local.bat` on your PC instead (needs Python 3 + Git for
Windows; schedule it in Task Scheduler), then Actions → Nightly → Run workflow with **skip_scrape** ticked to publish.

## End of season — do this before March
The live CSVs only ever hold the **current** year. When next season's first run happens, 2026 would disappear from the
site unless it has been folded into the parquet: append 2026 to the combined CSVs, run `make_bppa_data.bat`, and copy
the new `bppa_data.parquet` / `bppa_pitstd.parquet` into `docs/` (track 50 of the project has the steps).

## Local commands
```
pip install -r requirements.txt
python scraper/bref_current.py --out docs          # scrape
python build/leaderboards.py --data docs --out wp  # build pages
python tests/test_all.py                           # checks
python build/publish_wp.py --dry-run               # needs the 4 WPCOM_* env vars
```
