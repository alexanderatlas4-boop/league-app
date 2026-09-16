"""Pull the current season from ESPN into the database. Run weekly (GitHub Actions) or by hand.

Needs env vars: DATABASE_URL, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID.
Optional: SEASON (defaults to this year), START (first season to refresh, defaults to SEASON).
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import db  # noqa: E402
from core.espn_export import espn  # noqa: E402


def run(start=None, end=None, url=None):
    league = os.environ["ESPN_LEAGUE_ID"]
    end = int(end or os.environ.get("SEASON") or date.today().year)
    start = int(start or os.environ.get("START") or end)
    rows = espn(league, start, end, os.environ.get("ESPN_S2"), os.environ.get("ESPN_SWID"))
    eng = db.engine(url)
    db.init(eng)
    seasons, n_games, n_strength = db.replace_seasons(eng, rows)
    teams = {r[2] for r in rows if r[6] not in ("strength",)} | {r[4] for r in rows if r[6] not in ("strength",)}
    ren = db.load_renames(eng)
    db.ensure_accounts(eng, {ren.get(t, t) for t in teams if t})
    print(f"Saved seasons {seasons}: {n_games} game rows, {n_strength} roster projections.")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(*(args + [None, None])[:2])
