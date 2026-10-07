"""Pull the current season from ESPN into the database. Run weekly (GitHub Actions) or by hand.

Needs env vars: DATABASE_URL, ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID.
Optional: SEASON (defaults to this year), START (first season to refresh, defaults to SEASON),
ANTHROPIC_API_KEY (lets Claude write the weekly column), COLUMN_MODEL.
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
    rename_from = os.environ.get("RENAME_FROM", "").strip()
    if rename_from:  # optional: show an ESPN name differently everywhere
        db.set_rename(eng, rename_from, os.environ.get("RENAME_TO", "").strip())
        print(f"Name change saved: {rename_from} -> {os.environ.get('RENAME_TO', '').strip() or '(removed)'}")
    seasons, n_games, n_strength = db.replace_seasons(eng, rows)
    teams = {r[2] for r in rows if r[6] not in ("strength",)} | {r[4] for r in rows if r[6] not in ("strength",)}
    ren = db.load_renames(eng)
    db.ensure_accounts(eng, {ren.get(t, t) for t in teams if t})
    print(f"Saved seasons {seasons}: {n_games} game rows, {n_strength} roster projections.")
    write_column(eng)


def write_column(eng):
    """Have Claude write this week's column. Never fails the refresh; the page has its own fallback."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("No ANTHROPIC_API_KEY set, so the page will write its own column.")
        return
    import time
    from core.model import League
    from core import column as col
    L = League(db.load_games(eng), db.load_strength(eng), db.load_renames(eng))
    R = L.recap()
    if not R:
        return
    try:
        text = col.write(L, key, os.environ.get("COLUMN_MODEL") or None)
    except Exception as e:
        print(f"Column skipped: {e}")
        return
    db.set_setting(eng, "column", dict(season=R["season"], week=R["week"], text=text,
                                       at=int(time.time() * 1000)))
    print(f"Claude wrote the week {R['week']} column ({len(text.split())} words).")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(*(args + [None, None])[:2])
