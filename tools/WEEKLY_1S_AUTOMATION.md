# Weekly 1-second pull: Cursor Automation

Every Saturday a Cursor Automation pulls the past 13 days of TopstepX 1-second MES/MNQ bars and opens a pull request on the private `intermarket-divergence-data` repo. The PR contains the new daily files, the updated `pull_log.csv`, the rebuilt `delta_1m/` files, and a change-log entry in `DATA_MANIFEST.md`.

TopstepX only keeps 1-second bars for 14 days. Pulling 13 days back means one missed Saturday loses nothing; two missed Saturdays in a row lose data.

## One-time setup

1. **Merge the scanner PR that adds `weekly_1s_to_repo.py`** into `main`, so the automation runs code from `main`.
2. **Secrets.** In the Cursor dashboard → Cloud Agents → Secrets, add `PROJECTX_USERNAME` and `PROJECTX_API_KEY`, scoped to both repos (or all repositories). The `.env` file is not in git, so the automation machine never sees it.
3. **GitHub access.** The Cursor GitHub app must have access to `charisjv19/intermarket-divergence-data` (private), with permission to push branches and open pull requests.
4. **Create the automation** (cursor.com/automations/new):
   - **Trigger:** Scheduled, cron `0 15 * * 6`. That is Saturday 15:00, which is 10:00 ET if the schedule runs in UTC; check the timezone the automation page shows.
   - **Repositories:** both `charisjv19/intermarket-divergence-scanner` (branch `main`) and `charisjv19/intermarket-divergence-data` (branch `main`). Scheduled automations have no repository unless you add one.
   - **Pull request creation:** on.
   - **Instructions:** paste the block below.
5. **Test it:** run the automation once by hand and check that a PR appears on the data repo.

**Merge each weekly PR before the next Saturday.** The next run starts from the data repo's `main`; if last week's PR is still open, both PRs add the same days and will conflict.

## Instructions to paste into the automation

```
You run the weekly TopstepX 1-second data pull. Follow these steps exactly. Do not improvise.

Repositories:
- SCANNER = the checkout of charisjv19/intermarket-divergence-scanner (code)
- DATA    = the checkout of charisjv19/intermarket-divergence-data (data)
Find both checkout paths first (e.g. with `ls` / `git remote -v`).

Rules:
- Never print, echo or log the values of PROJECTX_USERNAME or PROJECTX_API_KEY.
- Never edit any file in SCANNER.
- Never edit, create or delete data files in DATA by hand. Only the script below changes them.
- Never commit or push to main in either repository.
- If any step fails, stop and report the full error output. Do not try workarounds.

Step 1: credentials. Run:
  test -n "$PROJECTX_USERNAME" && test -n "$PROJECTX_API_KEY" && echo CREDENTIALS_OK || echo CREDENTIALS_MISSING
If it prints CREDENTIALS_MISSING, stop and report: "PROJECTX secrets were not injected into this
automation; nothing was pulled. Check Cursor Cloud Agent secrets." Do not look for credentials anywhere else.

Step 2: dependencies. In SCANNER run:
  python3 -m pip install -q -r requirements.txt

Step 3: data repo on latest main. In DATA run:
  git checkout main && git pull --ff-only
If git has no user identity configured, set one for this repo only:
  git config user.name "Weekly 1s automation" && git config user.email "automation@users.noreply.github.com"

Step 4: pull. In SCANNER run (replace DATA_PATH with the DATA checkout path):
  python3 -W ignore::RuntimeWarning -m backtesting_toolkit.weekly_1s_to_repo --data-repo DATA_PATH --branch "data/weekly-1s-$(date -u +%Y-%m-%d)" --update-delta --update-manifest
The last line of output starts with RESULT:.

Step 5: pull request.
- If RESULT shows pushed=yes: open a pull request in charisjv19/intermarket-divergence-data from
  the branch named in RESULT into main.
  Title: "Weekly 1s pull: <today's date, YYYY-MM-DD>"
  Body: the summary from the RESULT line, then the per-day lines the script printed (date, bars,
  volume, contract), then any lines containing "no bars" or "WARNING".
- If RESULT shows pushed=no: do not open a pull request. Report "Nothing new to save this week."

Step 6: report. Finish with a short summary: days added per symbol, the PR link (if any), and any
warnings or "no bars" days (holidays are expected; weekdays with no bars should be flagged).
```

## Running it by hand

From the scanner repo, with the data repo cloned at `~/intermarket-divergence-data`:

```
python3 -m backtesting_toolkit.weekly_1s_to_repo --data-repo ~/intermarket-divergence-data \
    --branch "data/weekly-1s-$(date -u +%Y-%m-%d)" --update-delta --update-manifest
```

Then open the PR on GitHub. Without `--branch`, the script commits to the data repo's current branch and pushes it directly.
