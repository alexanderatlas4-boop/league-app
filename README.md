# League record book + sportsbook

A Streamlit app for the league: all-time stats, weekly recap and preview, pick'em, and a play-money sportsbook.
Data lives in Supabase (Postgres). A GitHub Action pulls ESPN every Tuesday morning.

## One-time setup (about 20 minutes)

1. **Database (Supabase, free).** Create a project at supabase.com and save the database password.
   Click **Connect** and copy the **Session pooler** connection string (the direct one won't work from
   Streamlit Cloud or GitHub). Replace `[YOUR-PASSWORD]` in it. If the password has symbols like `@` or `#`,
   pick one without them to avoid encoding trouble.
2. **Code (GitHub).** Create a private repo and upload everything in this folder.
3. **App (Streamlit Community Cloud, free).** share.streamlit.io > Create app > pick the repo, `app.py`.
   Under Advanced settings choose Python 3.12 and paste the secrets from `.streamlit/secrets.toml.example`
   with your real values.
4. **Load history.** Open the app, sidebar > Commissioner > enter your `ADMIN_PASSWORD` > Commissioner page >
   upload your `league_games.csv` > Load this file. Then under **Names**, map `Alex Atlas` to `Alex Atlas & Leo`.
5. **Weekly refresh.** In the GitHub repo: Settings > Secrets and variables > Actions > add
   `DATABASE_URL`, `ESPN_LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID`. Then Actions > Weekly ESPN refresh > Run workflow
   once to confirm it works.
6. Share the app link with the league.

## Every week
Nothing. The Tuesday job loads results and next week's matchups; bets and picks grade themselves.
Use **Pull from ESPN now** on the Commissioner page for an extra refresh (for example Thursday, for injury news).

## Sign-in
Each team's password starts as its team name (capitals and spaces don't matter). Anyone can change their own
password in the sidebar; the commissioner can reset any team's.

## Local testing
```
pip install -r requirements.txt
python scripts/setup_db.py league_games.csv --rename "Alex Atlas=Alex Atlas & Leo"   # creates league.db
streamlit run app.py
```
Without `DATABASE_URL`, everything uses a local `league.db` file.

## When ESPN cookies expire
The job fails with "access denied". Copy fresh `espn_s2` and `SWID` from your browser and update the GitHub
(and Streamlit) secrets.
