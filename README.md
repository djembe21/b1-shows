# b1.shows

Indie shows in Korea, in English, without scrolling Instagram.

Every morning this:

1. checks the recent public posts of the venues, bands and promoters in `watchlist.yaml` (through Meta's official Business Discovery API)
2. has Claude read each new caption and poster (Korean is fine) and pull out the date, time, venue, lineup, price and ticket link
3. merges duplicates (the venue, each band and the promoter often post the same show)
4. publishes a mobile-friendly website plus a calendar feed people can subscribe to

It all runs free on GitHub (Actions + Pages). The only running cost is the Claude API, which stays small because each post is read once.

```
watchlist.yaml          <- the accounts to check (the part you'll edit most)
gigradar/
  instagram.py          Business Discovery fetcher
  extract.py            Claude prompt + schema for reading posts/posters
  store.py              venue matching + duplicate merging
  publish.py            writes docs/events.json and docs/calendar.ics
  run.py                the daily pipeline
  token_helper.py       one-time helper for getting your Meta token
docs/                   the public website (GitHub Pages serves this folder)
data/                   events + which posts were already read (committed by the bot)
.github/workflows/      the daily schedule
tests/                  offline tests + sample data
```

---

## Setup (about an hour, once)

### 1. Instagram + Facebook side

Business Discovery only works through an Instagram **professional** account that's linked to a **Facebook Page**. A separate project account works best (say `@seoulgigradar`): it keeps this off your personal account, and later it could repost the weekly highlights.

1. Create the Instagram account, then in its settings switch it to a **Creator** or **Business** account.
2. Create a Facebook Page for it and link the Instagram account to the Page (Page settings → Linked accounts → Instagram).

### 2. Meta developer app

1. Go to [developers.facebook.com](https://developers.facebook.com), log in, and **Create app**. Pick the use case for managing Instagram/Pages, with app type **Business**. (Meta renames these screens often, so the labels may differ a bit.)
2. In the app, add the **Instagram** product and choose the **Instagram API with Facebook Login** setup (not "Instagram Login": Business Discovery only exists on the Facebook Login side).
3. Open **Tools → Graph API Explorer**, select your app, and generate a User token with these permissions: `instagram_basic`, `pages_show_list`, `pages_read_engagement`, `business_management`.
4. Copy the App ID and App Secret (App settings → Basic) and run the helper on your computer:

   ```bash
   pip install -r requirements.txt
   python -m gigradar.token_helper --app-id APP_ID --app-secret APP_SECRET --token THE_EXPLORER_TOKEN
   ```

   It prints your **IG_USER_ID** and a **Page token**. A Page token made from a long-lived user token normally doesn't expire. Save both.

You can leave the app in Development mode, since you're the only one logging into it. If Meta ever requires App Review for this, that's the step to do next.

### 3. Claude API key

Create a key at [console.anthropic.com](https://console.anthropic.com) and **set a monthly spend limit** there so there are no surprises.

### 4. GitHub

1. Create a new repository and upload this folder.
2. **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `IG_USER_ID`
   - `IG_ACCESS_TOKEN` (the Page token)
   - `ANTHROPIC_API_KEY`
3. **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/docs`.
4. **Actions → Update gig listings → Run workflow** to do the first run by hand.

The site appears at `https://YOUR-NAME.github.io/REPO-NAME/`. After that it updates itself at about 6am KST daily. If a run fails (usually an expired token), GitHub emails you.

### 5. Check the first run

Open `data/fetch_report.json`. Every handle shows `ok` with post counts, or `error` with the reason. `Invalid user id`-type errors mean the account is personal (or the handle is wrong), so the API can't see it. Swap in the venue instead.

---

## Building the watchlist

Edit `watchlist.yaml`. **Start with venues**: most bands use personal accounts the API can't see, but venues almost always post their lineups. Then add promoters and the bands you care about.

For each venue, add `aliases`: the Korean name and other spellings that show up on posters. That's how the same show posted by a band ("클럽에프에프") and the venue ("Club FF") gets merged into one listing.

A cheap way to find accounts: open a venue's Instagram and look at who they tag in show posts.

Business Discovery has hourly rate limits, so keep the list to a few hundred accounts. That covers a lot of venues.

---

## Running locally

```bash
pip install -r requirements.txt
python -m unittest discover tests     # offline tests
python -m gigradar.run --dry-run      # full pipeline on sample data, no API calls
cd docs && python -m http.server      # then open http://localhost:8000
```

With your secrets exported as env vars, `python -m gigradar.run --check` confirms the Instagram token works, and `python -m gigradar.run` does a real run.

Note: `--dry-run` writes sample shows into `data/` and `docs/`. Don't commit those. Run `git checkout data docs` afterwards.

---

## Tuning

- **Model**: defaults to `claude-sonnet-5-5`, which reads Korean posters well. Set a `GIGRADAR_MODEL` env var in the workflow to try a cheaper model.
- **Prompt**: the rules Claude follows are in `SYSTEM` in `gigradar/extract.py`. If it keeps misreading something (a venue's poster style, a recurring event), add a line there.
- **Low-confidence listings** get a "check post" tag on the site. Anything under 0.4 confidence is dropped.
- **"Suggest a venue" link**: set `SUGGEST_URL` at the top of the script in `docs/index.html` (a Google Form or your repo's new-issue page).

## Being a good neighbor

- Every listing links back to the original post, and the site never re-hosts posters. Venues and bands still get the click.
- If a venue or band asks to be left off, remove their handle from `watchlist.yaml`.
- Listings come from public posts but get read by a model, so the site tells people to check the original post before they go.
