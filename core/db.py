"""Storage. Works with Postgres (Supabase) in production and SQLite locally.

Set DATABASE_URL (env var or Streamlit secret). Without it, a local league.db file is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time

import sqlalchemy as sa

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS games (
        season INTEGER NOT NULL, week INTEGER NOT NULL,
        team_a TEXT NOT NULL, score_a DOUBLE PRECISION, team_b TEXT NOT NULL, score_b DOUBLE PRECISION,
        type TEXT NOT NULL, proj_a DOUBLE PRECISION, proj_b DOUBLE PRECISION,
        PRIMARY KEY (season, week, team_a, team_b, type))""",
    """CREATE TABLE IF NOT EXISTS strength (
        season INTEGER NOT NULL, team TEXT NOT NULL, points DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (season, team))""",
    """CREATE TABLE IF NOT EXISTS renames (espn_name TEXT PRIMARY KEY, display_name TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS accounts (team TEXT PRIMARY KEY, salt TEXT NOT NULL, pw_hash TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS picks (
        season INTEGER NOT NULL, week INTEGER NOT NULL, team_name TEXT NOT NULL, game_key TEXT NOT NULL,
        pick TEXT NOT NULL, created_at BIGINT NOT NULL,
        PRIMARY KEY (season, week, team_name, game_key))""",
    """CREATE TABLE IF NOT EXISTS bets (
        id TEXT PRIMARY KEY, team_name TEXT NOT NULL, market_key TEXT NOT NULL, market_id TEXT NOT NULL,
        side TEXT NOT NULL, team TEXT, line DOUBLE PRECISION, price INTEGER NOT NULL, stake INTEGER NOT NULL,
        status TEXT NOT NULL, grade TEXT, created_at BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS refresh_log (at BIGINT NOT NULL, note TEXT NOT NULL)""",
]

DEFAULTS = {
    "league_name": "Mickey Mouse Fantasy League",
    "bankroll": 1000,
    "top_up": 100,
    "playoff_teams": 0,            # 0 = infer from past seasons
    "kickoff": {"2026": "2026-09-10"},
    "lock_at": "20:15",
    "allow_self": False,
    "require_approval": False,
}


def database_url(explicit: str | None = None) -> str:
    url = str(explicit or os.environ.get("DATABASE_URL") or "sqlite:///league.db").strip()
    if url.upper().startswith("DATABASE_URL"):      # pasted as a whole TOML line
        url = url.split("=", 1)[1].strip()
    url = url.strip().strip('"').strip("'").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def engine(url: str | None = None):
    return sa.create_engine(database_url(url), pool_pre_ping=True, future=True)


def init(eng):
    with eng.begin() as c:
        for stmt in SCHEMA:
            c.execute(sa.text(stmt))


def rows(eng, sql, **params):
    with eng.connect() as c:
        return [dict(r._mapping) for r in c.execute(sa.text(sql), params)]


def execute(eng, sql, params=None):
    with eng.begin() as c:
        c.execute(sa.text(sql), params or {})


def now_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------- league data
def load_games(eng):
    out = []
    for r in rows(eng, "SELECT * FROM games"):
        out.append(dict(season=r["season"], week=r["week"], a=r["team_a"], sa=r["score_a"], b=r["team_b"],
                        sb=r["score_b"], type=r["type"], pa=r["proj_a"], pb=r["proj_b"]))
    return out


def load_strength(eng):
    out = {}
    for r in rows(eng, "SELECT * FROM strength"):
        out.setdefault(r["season"], {})[r["team"]] = r["points"]
    return out


def load_renames(eng):
    return {r["espn_name"]: r["display_name"] for r in rows(eng, "SELECT * FROM renames")}


def replace_seasons(eng, export_rows, note=""):
    """Replace every season present in export_rows with the new data (one transaction)."""
    games, strength = [], []
    for r in export_rows:
        r = list(r) + ["", ""]
        s, w, a, sa_, b, sb, t = r[:7]
        num = lambda v: None if v in ("", None) else float(v)
        if t == "strength":
            strength.append(dict(season=int(s), team=a, points=float(sa_)))
        else:
            games.append(dict(season=int(s), week=int(w), team_a=a, score_a=num(sa_), team_b=b, score_b=num(sb),
                              type=t, proj_a=num(r[7]), proj_b=num(r[8])))
    seasons = sorted({g["season"] for g in games})
    if not seasons:
        raise ValueError("No games in the export; nothing was changed.")
    with eng.begin() as c:
        for s in seasons:
            c.execute(sa.text("DELETE FROM games WHERE season = :s"), dict(s=s))
            c.execute(sa.text("DELETE FROM strength WHERE season = :s"), dict(s=s))
        c.execute(sa.text("""INSERT INTO games (season, week, team_a, score_a, team_b, score_b, type, proj_a, proj_b)
                             VALUES (:season, :week, :team_a, :score_a, :team_b, :score_b, :type, :proj_a, :proj_b)"""), games)
        if strength:
            c.execute(sa.text("INSERT INTO strength (season, team, points) VALUES (:season, :team, :points)"), strength)
        c.execute(sa.text("INSERT INTO refresh_log (at, note) VALUES (:at, :note)"),
                  dict(at=now_ms(), note=note or f"seasons {seasons[0]}-{seasons[-1]}, {len(games)} rows"))
    return seasons, len(games), len(strength)


def last_refresh(eng):
    r = rows(eng, "SELECT at, note FROM refresh_log ORDER BY at DESC LIMIT 1")
    return r[0] if r else None


# ---------------------------------------------------------------- settings
def get_settings(eng):
    s = dict(DEFAULTS)
    for r in rows(eng, "SELECT key, value FROM settings"):
        s[r["key"]] = json.loads(r["value"])
    return s


def set_setting(eng, key, value):
    with eng.begin() as c:
        c.execute(sa.text("DELETE FROM settings WHERE key = :k"), dict(k=key))
        c.execute(sa.text("INSERT INTO settings (key, value) VALUES (:k, :v)"), dict(k=key, v=json.dumps(value)))


def set_rename(eng, espn_name, display_name):
    with eng.begin() as c:
        c.execute(sa.text("DELETE FROM renames WHERE espn_name = :e"), dict(e=espn_name))
        if display_name and display_name != espn_name:
            c.execute(sa.text("INSERT INTO renames (espn_name, display_name) VALUES (:e, :d)"), dict(e=espn_name, d=display_name))


# ---------------------------------------------------------------- accounts
def norm_pw(pw):
    return re.sub(r"\s+", "", str(pw or "").strip().lower())


def _hash(salt, pw):
    return hashlib.pbkdf2_hmac("sha256", norm_pw(pw).encode(), bytes.fromhex(salt), 120_000).hex()


def set_password(eng, team, pw):
    salt = secrets.token_hex(16)
    with eng.begin() as c:
        c.execute(sa.text("DELETE FROM accounts WHERE team = :t"), dict(t=team))
        c.execute(sa.text("INSERT INTO accounts (team, salt, pw_hash) VALUES (:t, :s, :h)"), dict(t=team, s=salt, h=_hash(salt, pw)))


def check_password(eng, team, pw):
    r = rows(eng, "SELECT salt, pw_hash FROM accounts WHERE team = :t", t=team)
    return bool(r) and secrets.compare_digest(_hash(r[0]["salt"], pw), r[0]["pw_hash"])


def teams_with_accounts(eng):
    return {r["team"] for r in rows(eng, "SELECT team FROM accounts")}


def ensure_accounts(eng, teams):
    """Any team without an account gets its own name as the password."""
    have = teams_with_accounts(eng)
    for t in teams:
        if t not in have:
            set_password(eng, t, t)


# ---------------------------------------------------------------- picks & bets
def load_picks(eng):
    return rows(eng, "SELECT * FROM picks")


def save_picks(eng, season, week, team, picks: dict):
    with eng.begin() as c:
        c.execute(sa.text("DELETE FROM picks WHERE season = :s AND week = :w AND team_name = :t"), dict(s=season, w=week, t=team))
        c.execute(sa.text("""INSERT INTO picks (season, week, team_name, game_key, pick, created_at)
                             VALUES (:s, :w, :t, :k, :p, :at)"""),
                  [dict(s=season, w=week, t=team, k=k, p=v, at=now_ms()) for k, v in picks.items()])


def load_bets(eng):
    return rows(eng, "SELECT * FROM bets ORDER BY created_at")


def add_bet(eng, bet):
    b = dict(bet)
    b.setdefault("id", secrets.token_hex(8))
    b.setdefault("created_at", now_ms())
    b.setdefault("grade", None)
    execute(eng, """INSERT INTO bets (id, team_name, market_key, market_id, side, team, line, price, stake, status, grade, created_at)
                    VALUES (:id, :team_name, :market_key, :market_id, :side, :team, :line, :price, :stake, :status, :grade, :created_at)""", b)
    return b["id"]


def update_bet(eng, bet_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("status", "grade")}
    if not allowed: return
    sets = ", ".join(f"{k} = :{k}" for k in allowed)
    execute(eng, f"UPDATE bets SET {sets} WHERE id = :id", dict(allowed, id=bet_id))
