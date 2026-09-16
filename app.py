"""League record book + sportsbook.

The page itself (ui/index.html) is the same design as the original record book. This file is the
server behind it: it loads data from the database, sets the official lines, checks sign-ins,
and saves picks, bets and commissioner changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core import db
from core.model import League, against_self, lock_time, now_utc, profit

st.set_page_config(page_title="League record book", page_icon="🏈", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {display:none !important}
.block-container, [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"] {padding:0 !important; max-width:100% !important}
[data-testid="stVerticalBlock"] {gap:0 !important}
iframe[title="league_ui.league_ui"], iframe[title="league_ui"], [data-testid="stCustomComponentV1"] {height:100vh !important; width:100% !important; display:block; border:0}
</style>""", unsafe_allow_html=True)

UI = components.declare_component("league_ui", path=str(Path(__file__).parent / "ui"))


def secret(name, default=None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


@st.cache_resource
def get_engine():
    eng = db.engine(secret("DATABASE_URL"))
    db.init(eng)
    return eng


ENG = get_engine()
ss = st.session_state
ss.setdefault("team", None)
ss.setdefault("admin", False)
ss.setdefault("last_action", None)
ss.setdefault("notice", None)


@st.cache_resource(max_entries=4, show_spinner=False)
def get_league(version):
    return League(db.load_games(ENG), db.load_strength(ENG), db.load_renames(ENG))


@st.cache_resource(max_entries=8, show_spinner=False)
def get_season(version, playoff_teams):
    return get_league(version).season_markets(playoff_teams or None)


def data_version():
    r = db.last_refresh(ENG)
    return f"{r['at'] if r else 0}|{sorted(db.load_renames(ENG).items())}"


def notify(text, kind="ok"):
    ss.notice = (text, kind, f"{time.time():.6f}")


def is_locked(L, SET, season, week):
    if any(g["season"] == season and g["week"] == week for g in L.games):
        return True
    if (SET.get("manual_locks") or {}).get(f"{season}|{week}"):
        return True
    t = lock_time((SET.get("kickoff") or {}).get(str(season)), SET.get("lock_at", "20:15"), week)
    return bool(t and now_utc() >= t)


def market_locked(L, SET, mk):
    s, w = mk.split("|")
    if w == "season":
        info = L.season_info()
        return info["season"] != int(s) or info["started"] or info["done"]
    return is_locked(L, SET, int(s), int(w))


def official_lines(L, SET, version):
    """The lines bettors see and bet against: the model's, unless the commissioner adjusted them."""
    overrides = SET.get("line_overrides") or {}
    wm = L.week_markets()
    sm, sim = get_season(version, int(SET.get("playoff_teams") or 0))
    week = season = None
    if wm:
        week = dict(mk=wm["mk"], s=wm["season"], w=wm["week"], markets=wm["markets"])
    if sm:
        race = [dict(m=r["m"], rec=r["record"], roster=r["roster"], hist=r["hist"], wins=r["wins"], po=r["po"],
                     bye=r["bye"], final=r["final"], title=r["title"]) for r in sim["race"]]
        season = dict(mk=sm["mk"], s=sm["season"], w="season", markets=sm["markets"], race=race,
                      usedRoster=sim["used_roster"], P=sim["P"], byes=sim["byes"])
    effective = {}
    for x in (week, season):
        if x:
            effective[x["mk"]] = (overrides.get(x["mk"]) or {}).get("markets") or x["markets"]
    return week, season, effective, overrides


def page_data(L, SET, overrides):
    games = []
    for g in db.load_games(ENG):
        row = [g["season"], g["week"], g["a"], g["sa"], g["b"], g["sb"], g["type"]]
        if g["type"] == "scheduled" and (g["pa"] or g["pb"]):
            row.append([g["pa"], g["pb"]])
        games.append(row)
    for s, teams in db.load_strength(ENG).items():
        for t, v in teams.items():
            games.append([s, 0, t, v, "", None, "strength"])
    picks = {}
    for p in db.load_picks(ENG):
        wk = picks.setdefault(f"{p['season']}|{p['week']}", {})
        e = wk.setdefault(p["team_name"], {"p": {}, "at": p["created_at"]})
        e["p"][p["game_key"]] = p["pick"]
        e["at"] = max(e["at"], p["created_at"])
    bets = [dict(id=b["id"], who=b["team_name"], mk=b["market_key"], market=b["market_id"], side=b["side"], team=b["team"],
                 line=b["line"], price=b["price"], stake=b["stake"], at=b["created_at"], status=b["status"], grade=b["grade"])
            for b in db.load_bets(ENG)]
    return dict(
        name=SET.get("league_name") or "Home League",
        games=games,
        renames=db.load_renames(ENG),
        settings=dict(bankroll=SET.get("bankroll"), topUp=SET.get("top_up"), playoffTeams=SET.get("playoff_teams") or 0,
                      kickoff=SET.get("kickoff") or {}, lockAt=SET.get("lock_at"), allowSelf=bool(SET.get("allow_self")),
                      requireApproval=bool(SET.get("require_approval")), maxBet=int(SET.get("max_bet") or 100)),
        locks={k: 1 for k, v in (SET.get("manual_locks") or {}).items() if v},
        picks=picks,
        book=dict(posted=overrides, bets=bets),
    )


# ---------------------------------------------------------------- actions
def handle(a, L, SET, effective):
    kind = a.get("type")
    if kind == "signin":
        team = a.get("team")
        if team in L.managers and db.check_password(ENG, team, a.get("password")):
            ss.team = team
            notify(f"Signed in as {team}.")
        else:
            notify("Team or password doesn't match.", "err")
    elif kind == "signout":
        ss.team = None
        notify("Signed out.")
    elif kind == "password":
        if not ss.team:
            notify("Sign in first.", "err")
        elif len(db.norm_pw(a.get("password"))) < 4:
            notify("Use at least 4 characters.", "err")
        else:
            db.set_password(ENG, ss.team, a["password"])
            notify("Password changed.")
    elif kind == "admin":
        if secret("ADMIN_PASSWORD") and a.get("password") == str(secret("ADMIN_PASSWORD")):
            ss.admin = True
            notify("Commissioner tools unlocked. They're at the bottom of the Sportsbook tab.")
        else:
            notify("That isn't the commissioner password.", "err")
    elif kind == "admin_off":
        ss.admin = False
        notify("Commissioner tools locked.")
    elif kind == "picks":
        place_picks(a, L, SET)
    elif kind == "bet":
        place_bet(a, L, SET, effective)
    elif kind in ("admin_save", "import", "pw_reset", "refresh"):
        if not ss.admin:
            notify("Commissioner tools are locked.", "err")
        elif kind == "admin_save":
            admin_save(a, SET)
        elif kind == "import":
            import_rows(a)
        elif kind == "pw_reset":
            team = a.get("team")
            if team not in L.managers:
                notify("Pick a team.", "err")
            else:
                db.set_password(ENG, team, a.get("password") or team)
                notify(f"Password set for {team}.")
        else:
            pull_espn(a, L)


def place_picks(a, L, SET):
    nw = L.next_week()
    if not ss.team:
        return notify("Sign in first.", "err")
    if not nw or (a.get("s"), a.get("w")) != (nw[0], nw[1]):
        return notify("Those picks are for a different week. Reload the page.", "err")
    s, w, games = nw
    if is_locked(L, SET, s, w):
        return notify("Too late, picks are locked.", "err")
    from core.model import gkey
    valid = {gkey(g): {g["a"], g["b"]} for g in games}
    picks = a.get("picks") or {}
    if set(picks) != set(valid) or any(v not in valid[k] for k, v in picks.items()):
        return notify("Pick a winner in every game.", "err")
    db.save_picks(ENG, s, w, ss.team, picks)
    notify("Picks saved.")


def place_bet(a, L, SET, effective):
    me = ss.team
    if not me:
        return notify("Sign in first.", "err")
    mk = a.get("mk") or ""
    markets = effective.get(mk)
    if not markets:
        return notify("Those lines aren't open anymore.", "err")
    if market_locked(L, SET, mk):
        return notify("Betting on that is locked.", "err")
    m = next((x for x in markets if x["id"] == a.get("market")), None)
    side = m and next((x for x in m["sides"] if x["id"] == a.get("side")), None)
    if not side:
        return notify("That bet isn't available.", "err")
    if side["price"] != a.get("price") or side.get("line") != a.get("line"):
        return notify("The line just moved. Check the new number and try again.", "err")
    err = against_self(me, m["id"], side["id"], side.get("team"), bool(SET.get("allow_self")))
    if err:
        return notify(err, "err")
    max_bet = int(SET.get("max_bet") or 100)
    try:
        raw = float(a.get("stake") or 0)
    except (TypeError, ValueError):
        raw = 0
    stake = int(raw)
    if raw != stake or not 1 <= stake <= max_bet:
        return notify(f"Bets run from $1 to ${max_bet:,}, in whole dollars.", "err")
    status = "pending" if SET.get("require_approval") else "open"
    db.add_bet(ENG, dict(team_name=me, market_key=mk, market_id=m["id"], side=side["id"], team=side.get("team"),
                         line=side.get("line"), price=int(side["price"]), stake=stake, status=status))
    notify(("Bet sent for approval: " if status == "pending" else "Bet placed: ") +
           f"${stake:,} to win ${profit(stake, side['price']):,.2f}.")


def admin_save(a, SET):
    st_ = a.get("settings")
    if st_:
        mapping = dict(bankroll="bankroll", topUp="top_up", playoffTeams="playoff_teams", lockAt="lock_at",
                       allowSelf="allow_self", requireApproval="require_approval", kickoff="kickoff", maxBet="max_bet")
        for k, v in st_.items():
            if k in mapping and v is not None:
                db.set_setting(ENG, mapping[k], v)
    if a.get("locks") is not None:
        db.set_setting(ENG, "manual_locks", {k: True for k in (a.get("locks") or {})})
    current = {b["id"]: b for b in db.load_bets(ENG)}
    for b in a.get("bets") or []:
        old = current.get(b.get("id"))
        if not old:
            continue
        status = b.get("status") if b.get("status") in ("open", "pending", "rejected") else old["status"]
        grade = b.get("grade") if b.get("grade") in ("win", "loss", "push", "void") else None
        if status != old["status"] or grade != old["grade"]:
            db.update_bet(ENG, b["id"], status=status, grade=grade)
    posted = a.get("posted")
    if isinstance(posted, dict):
        clean = {k: dict(at=v.get("at"), markets=v.get("markets")) for k, v in posted.items()
                 if isinstance(v, dict) and isinstance(v.get("markets"), list)}
        db.set_setting(ENG, "line_overrides", clean)
    notify("Saved.")


def import_rows(a):
    rows = []
    for r in a.get("rows") or []:
        r = list(r) + [None] * 8
        proj = r[7] if isinstance(r[7], list) else [None, None]
        rows.append([r[0], r[1], r[2], "" if r[3] is None else r[3], r[4] or "", "" if r[5] is None else r[5], r[6],
                     "" if proj[0] is None else proj[0], "" if proj[1] is None else proj[1]])
    try:
        seasons, n, k = db.replace_seasons(ENG, rows, note="imported from the page")
    except ValueError as e:
        return notify(str(e), "err")
    old = db.load_renames(ENG)
    new = a.get("renames") or {}
    for e in set(old) - set(new):
        db.set_rename(ENG, e, "")
    for e, d in new.items():
        db.set_rename(ENG, e, d)
    if a.get("name"):
        db.set_setting(ENG, "league_name", a["name"])
    teams = {x for r in rows if r[6] != "strength" for x in (r[2], r[4]) if x}
    db.ensure_accounts(ENG, {new.get(t, t) for t in teams})
    notify(f"Loaded seasons {seasons[0]}–{seasons[-1]} ({n} rows).")


def pull_espn(a, L):
    if not secret("ESPN_LEAGUE_ID"):
        return notify("Add ESPN_LEAGUE_ID, ESPN_S2 and ESPN_SWID to the app's secrets first.", "err")
    from core.espn_export import espn
    end = L.current_season()
    start = int(a.get("start") or end)
    try:
        rows = espn(str(secret("ESPN_LEAGUE_ID")), min(start, end), end, secret("ESPN_S2"), secret("ESPN_SWID"))
        seasons, n, k = db.replace_seasons(ENG, rows, note=f"manual pull {min(start, end)}-{end}")
    except SystemExit as e:
        return notify(f"ESPN refused the request: {e}", "err")
    except Exception as e:
        return notify(f"Pull failed: {e}", "err")
    ren = db.load_renames(ENG)
    db.ensure_accounts(ENG, {ren.get(x, x) for r in rows if r[6] != "strength" for x in (r[2], r[4]) if x})
    notify(f"Pulled {seasons[0]}–{seasons[-1]}: {n} rows, {k} roster projections.")


# ---------------------------------------------------------------- page
SET = db.get_settings(ENG)
VERSION = data_version()
L = get_league(VERSION)
week, season, effective, overrides = official_lines(L, SET, VERSION)
data = page_data(L, SET, overrides)
lines = dict(week=week, season=season)
version = hashlib.sha1(json.dumps([data, lines], sort_keys=True, default=str).encode()).hexdigest()
lr = db.last_refresh(ENG)
notice = ss.notice
action = UI(
    data=data, lines=lines, version=version, me=ss.team, admin=bool(ss.admin),
    last_refresh=("Last update: " + datetime.fromtimestamp(lr["at"] / 1000).strftime("%b %d, %Y %I:%M %p UTC") + f" ({lr['note']})." if lr else ""),
    notice=notice[0] if notice else None, notice_kind=notice[1] if notice else None, notice_id=notice[2] if notice else None,
    key="league_ui", default=None,
)
if isinstance(action, dict) and action.get("id") and action["id"] != ss.last_action:
    ss.last_action = action["id"]
    handle(action, L, SET, effective)
    get_league.clear()
    get_season.clear()
    st.rerun()
