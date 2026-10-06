# UO NA Events

Phone-installable calendar of official UO events on the NA shards, with calendar alerts.
Free hosting on GitHub Pages; GitHub Actions refreshes the data every 3 hours.

## Setup (once, ~10 min)
1. Create a new **public** GitHub repo and upload everything in this folder (keep `.github/` and `docs/`).
2. Repo **Settings > Pages**: Source = *Deploy from a branch*, Branch = `main`, Folder = `/docs`. Save.
3. **Actions** tab: enable workflows, open *Update UO events*, click **Run workflow**.
4. Your app is at `https://<username>.github.io/<repo>/`.

## On your phone
- **Install:** iPhone: open the link in Safari > Share > *Add to Home Screen*. Android: Chrome > menu > *Install app*.
- **Alerts:** tap *Get alerts* at the bottom of the app. This subscribes your phone calendar to the
  NA feed, with a reminder 30 min before each event (change `ALERT_MINUTES` in `build.py`).
  Google Calendar ignores the feed's built-in reminder: open that calendar's settings and set a default notification.

## Tweaks
- Shards / abbreviations: `SHARDS` in `build.py`.
- Refresh rate: the cron line in `.github/workflows/update.yml`.
- Discord: add the app link to your channel topic or the existing bot's embed footer.
