"""One-time setup: create tables and load history from a league_games.csv export.

    DATABASE_URL=... python scripts/setup_db.py league_games.csv
    DATABASE_URL=... python scripts/setup_db.py league_games.csv --rename "Alex Atlas=Alex Atlas & Leo"
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import db  # noqa: E402


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path, renames = sys.argv[1], {}
    for i, a in enumerate(sys.argv):
        if a == "--rename" and i + 1 < len(sys.argv):
            old, new = sys.argv[i + 1].split("=", 1)
            renames[old.strip()] = new.strip()
    with open(path, newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        rows = [[r["season"], r["week"], r["manager_a"], r["score_a"], r["manager_b"], r["score_b"], r.get("type") or "regular",
                 r.get("proj_a", ""), r.get("proj_b", "")] for r in rdr]
    eng = db.engine()
    db.init(eng)
    for old, new in renames.items():
        db.set_rename(eng, old, new)
    seasons, n_games, n_strength = db.replace_seasons(eng, rows, note=f"imported {os.path.basename(path)}")
    ren = db.load_renames(eng)
    teams = {ren.get(r[2], r[2]) for r in rows if r[6] != "strength"} | {ren.get(r[4], r[4]) for r in rows if r[6] != "strength"}
    db.ensure_accounts(eng, teams)
    print(f"Loaded seasons {seasons[0]}-{seasons[-1]}: {n_games} game rows, {n_strength} roster projections, {len(teams)} teams.")
    print("Every team's password is its team name until changed in the app.")


if __name__ == "__main__":
    main()
