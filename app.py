"""League record book + sportsbook (Streamlit)."""
from __future__ import annotations

import html
import os
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from core import db
from core.model import (League, against_self, american, fmt_odds, fmt_spread, gkey, lock_time,
                        market_label, now_utc, profit, record)

st.set_page_config(page_title="League record book", page_icon="🏈", layout="wide")


# ---------------------------------------------------------------- setup
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


@st.cache_data(ttl=60, show_spinner=False)
def data_version():
    r = db.last_refresh(ENG)
    return f"{r['at']}" if r else "none"


@st.cache_resource(max_entries=4, show_spinner=False)
def get_league(version, renames_key):
    return League(db.load_games(ENG), db.load_strength(ENG), db.load_renames(ENG))


@st.cache_resource(max_entries=8, show_spinner="Simulating the season…")
def get_season(version, renames_key, playoff_teams):
    return get_league(version, renames_key).season_markets(playoff_teams or None)


def clear_caches():
    st.cache_data.clear()
    st.cache_resource.clear()


SET = db.get_settings(ENG)
REN_KEY = str(sorted(db.load_renames(ENG).items()))
VERSION = data_version()
L = get_league(VERSION, REN_KEY)
ss = st.session_state
ss.setdefault("team", None)
ss.setdefault("admin", False)

money = lambda n: ("−" if round(n) < 0 else "") + f"${abs(round(n)):,}"
pct = lambda x: f"{x:.3f}".lstrip("0") if x < 1 else "1.000"
pc = lambda x: ">99%" if x >= 0.995 else "<1%" if x < 0.005 else f"{round(x * 100)}%"
signed = lambda x, d=1: f"{x:+.{d}f}"

st.markdown("""
<style>
.rafters{border-top:6px solid #1F3A5F;display:flex;gap:12px;overflow-x:auto;padding:0 0 8px;margin:4px 0 18px}
.banner{flex:0 0 104px;min-height:150px;background:#1F3A5F;color:#F3F5F8;text-align:center;padding:12px 8px 34px;
  clip-path:polygon(0 0,100% 0,100% 100%,50% 84%,0 100%);font-family:"Arial Narrow",Arial,sans-serif}
.banner .yr{font-size:30px;font-weight:800;color:#E5B94E;line-height:1}
.banner .rule{width:34px;height:2px;background:#E5B94E;margin:7px auto}
.banner .who{font-size:17px;font-weight:700;line-height:1.1;overflow-wrap:anywhere}
.banner .ru{font-size:11px;opacity:.75;margin-top:5px}
.banner.open{background:transparent;border:2px dashed #9AA5B1;clip-path:none;color:#6B7785}
.banner.open .yr{color:#6B7785}
.gotw{display:inline-block;background:#1F3A5F;color:#E5B94E;font-weight:800;padding:1px 9px;border-radius:4px;font-size:13px}
</style>""", unsafe_allow_html=True)


def banners():
    if not L.seasons: return
    out = []
    for s in L.seasons:
        c = L.champs.get(s)
        if c:
            out.append(f'<div class="banner"><div class="yr">{s}</div><div class="rule"></div><div class="who">{html.escape(c)}</div>'
                       f'<div class="ru">over {html.escape(L.runners[s])}</div></div>')
        else:
            label = "In progress" if s == L.seasons[-1] else "No title game"
            out.append(f'<div class="banner open"><div class="yr">{s}</div><div class="rule"></div><div class="who">{label}</div></div>')
    st.markdown('<div class="rafters">' + "".join(out) + "</div>", unsafe_allow_html=True)


def week_lock(season, week):
    return lock_time((SET.get("kickoff") or {}).get(str(season)), SET.get("lock_at", "20:15"), week)


def is_locked(season, week):
    if any(g["season"] == season and g["week"] == week for g in L.games):
        return True
    manual = (SET.get("manual_locks") or {}).get(f"{season}|{week}")
    if manual: return True
    t = week_lock(season, week)
    return bool(t and now_utc() >= t)


def fmt_dt(t):
    return f"{t:%a %b} {t.day}, {t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def fmt_lock(t):
    return fmt_dt(t) + " ET" if t else "kickoff"


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title(SET.get("league_name") or "League")
    if ss.team:
        st.success(f"Signed in as **{ss.team}**")
        if st.button("Sign out", width="stretch"):
            ss.team = None
            st.rerun()
        with st.expander("Change my password"):
            with st.form("pw_change", clear_on_submit=True):
                new1 = st.text_input("New password", type="password")
                new2 = st.text_input("Again", type="password")
                if st.form_submit_button("Save password"):
                    if len(db.norm_pw(new1)) < 4:
                        st.error("Use at least 4 characters.")
                    elif db.norm_pw(new1) != db.norm_pw(new2):
                        st.error("Those don't match.")
                    else:
                        db.set_password(ENG, ss.team, new1)
                        st.success("Password changed.")
    else:
        with st.form("login"):
            team = st.selectbox("Your team", [""] + L.managers)
            pw = st.text_input("Password", type="password", help="Until you change it, your password is your team name.")
            if st.form_submit_button("Sign in", width="stretch"):
                if team and db.check_password(ENG, team, pw):
                    ss.team = team
                    st.rerun()
                else:
                    st.error("Team or password doesn't match.")
    pages = ["This week", "Sportsbook", "All-time standings", "Head to head", "Managers", "Record book", "Trends", "Seasons"]
    if ss.admin:
        pages.append("Commissioner")
    page = st.radio("Go to", pages, label_visibility="collapsed")
    with st.expander("Commissioner"):
        if ss.admin:
            st.caption("Commissioner tools are unlocked.")
            if st.button("Lock tools"):
                ss.admin = False
                st.rerun()
        else:
            code = st.text_input("Commissioner password", type="password")
            if st.button("Unlock"):
                if secret("ADMIN_PASSWORD") and code == secret("ADMIN_PASSWORD"):
                    ss.admin = True
                    st.rerun()
                else:
                    st.error("That isn't the commissioner password.")
    lr = db.last_refresh(ENG)
    if lr:
        st.caption("Data updated " + fmt_dt(datetime.fromtimestamp(lr["at"] / 1000)))

st.title(SET.get("league_name") or "League record book")
if L.seasons:
    st.caption(f"{len(L.seasons)} seasons ({L.seasons[0]} to {L.seasons[-1]}), "
               f"{sum(1 for g in L.games if g['type'] != 'consolation'):,} games, {len(L.managers)} managers")
banners()

if not L.games:
    st.info("No games yet. A commissioner can load history from the Commissioner page, or run scripts/setup_db.py.")


# ---------------------------------------------------------------- pages
def page_week():
    P = L.preview()
    picks = db.load_picks(ENG)
    if P:
        s, w = P["season"], P["week"]
        locked, lt = is_locked(s, w), week_lock(s, w)
        with st.container(border=True):
            st.subheader(f"Week {w} picks")
            st.caption("Picks are locked." if locked else f"Pick every winner before {fmt_lock(lt)}.")
            week_picks = [p for p in picks if p["season"] == s and p["week"] == w]
            mine = {p["game_key"]: p["pick"] for p in week_picks if p["team_name"] == ss.team}
            if locked:
                rows_ = []
                for c in P["cards"]:
                    a, b = c["game"]["a"], c["game"]["b"]
                    na = sum(1 for p in week_picks if p["game_key"] == c["key"] and p["pick"] == a)
                    nb = sum(1 for p in week_picks if p["game_key"] == c["key"] and p["pick"] == b)
                    rows_.append({"Matchup": f"{a} vs {b}", "Picks": f"{a} {na}, {b} {nb}"})
                st.dataframe(pd.DataFrame(rows_), hide_index=True, width="stretch")
            elif not ss.team:
                st.info("Sign in (sidebar) to make your picks.")
            else:
                with st.form("picks"):
                    choice = {}
                    for c in P["cards"]:
                        a, b = c["game"]["a"], c["game"]["b"]
                        opts = [a, b]
                        labels = {a: f"{a} ({round(c['pA'] * 100)}%)", b: f"{b} ({round((1 - c['pA']) * 100)}%)"}
                        idx = opts.index(mine[c["key"]]) if c["key"] in mine else None
                        choice[c["key"]] = st.radio(f"{a} vs {b}", opts, index=idx, horizontal=True,
                                                    format_func=lambda x, lb=labels: lb[x], key=f"pk_{c['key']}")
                    if st.form_submit_button("Save my picks", type="primary"):
                        if any(v is None for v in choice.values()):
                            st.error("Pick a winner in every game.")
                        elif is_locked(s, w):
                            st.error("Too late, picks just locked.")
                        else:
                            db.save_picks(ENG, s, w, ss.team, choice)
                            st.success("Picks saved.")
                            st.rerun()
                if mine:
                    st.caption("Your saved picks are selected above. You can change them until lock.")
            who = sorted({p["team_name"] for p in week_picks}, key=str.lower)
            st.caption(("In so far: " + ", ".join(who)) if who else "No picks in yet.")
            stand = L.pick_standings(s, picks)
            if stand:
                with st.expander(f"{s} pick'em standings"):
                    st.dataframe(pd.DataFrame([{"Manager": r["m"], "Correct": f"{r['correct']} of {r['total']}",
                                                "Weeks won": r["weeks_won"], "Latest": r["latest"]} for r in stand]),
                                 hide_index=True, width="stretch")

    R = L.recap()
    if R:
        st.header(f"{R['season']}, week {R['week']} recap")
        if len(R["rows"]) >= 3:
            cols = st.columns(6)
            blow = max(R["rows"], key=lambda r: r["margin"])
            close = min(R["rows"], key=lambda r: r["margin"])
            cols[0].metric("Top score", f"{R['top']['pf']:.2f}", R["top"]["m"], delta_color="off")
            cols[1].metric("Low score", f"{R['bottom']['pf']:.2f}", R["bottom"]["m"], delta_color="off")
            if R["unlucky"]:
                cols[2].metric("Unluckiest loss", f"{R['unlucky']['pf']:.2f}",
                               f"{R['unlucky']['m']}, beat {R['unlucky']['beat']} of {R['n'] - 1}", delta_color="off")
            if R["lucky"]:
                cols[3].metric("Luckiest win", f"{R['lucky']['pf']:.2f}",
                               f"{R['lucky']['m']}, beat {R['lucky']['beat']} of {R['n'] - 1}", delta_color="off")
            cols[4].metric("Blowout", f"+{blow['margin']:.2f}", blow["winner"], delta_color="off")
            cols[5].metric("Closest", f"+{close['margin']:.2f}", close["winner"], delta_color="off")
            st.caption(f"League average this week: {R['avg']:.1f}")
        st.dataframe(pd.DataFrame([{"Winner": r["winner"], "Score": f"{r['w_score']:.2f}", "Loser": r["loser"],
                                    "Their score": f"{r['l_score']:.2f}", "Margin": f"{r['margin']:.2f}",
                                    "Notes": "; ".join(r["notes"])} for r in R["rows"]]),
                     hide_index=True, width="stretch")
        if R["regular"]:
            now_t = L.standings_through(R["season"], R["week"])
            prev = {r["m"]: r["rank"] for r in L.standings_through(R["season"], R["week"] - 1)}
            st.subheader(f"Standings after week {R['week']}")
            st.dataframe(pd.DataFrame([{"#": r["rank"], "Manager": r["m"], "Record": record(r["w"], r["l"], r["t"]),
                                        "Points for": round(r["pf"], 1),
                                        "Move": ("▲ " + str(prev[r["m"]] - r["rank"])) if prev.get(r["m"], r["rank"]) > r["rank"]
                                        else ("▼ " + str(r["rank"] - prev[r["m"]])) if prev.get(r["m"], r["rank"]) < r["rank"] else "–"}
                                       for r in now_t]), hide_index=True, width="stretch")

    if P:
        st.header(f"{P['season']}, week {P['week']} preview")
        st.caption(MODEL_BLURB.format(sd=round(P["sd"])))
        cols = st.columns(2)
        for i, c in enumerate(P["cards"]):
            a, b = c["game"]["a"], c["game"]["b"]
            with cols[i % 2].container(border=True):
                if c.get("gotw"):
                    st.markdown('<span class="gotw">Game of the week</span>', unsafe_allow_html=True)
                st.markdown(f"**{a}** vs **{b}**")
                st.progress(c["pA"], text=f"{a} {round(c['pA'] * 100)}%  ·  {b} {round((1 - c['pA']) * 100)}%")
                rec_ = lambda r: record(r["w"], r["l"], r["t"]) if r else "0–0"
                st.dataframe(pd.DataFrame([
                    {"Team": a, "Record": rec_(c["rec_a"]), "ESPN": c["A"]["espn"], "History": round(c["A"]["hist"], 1), "Projected": round(c["A"]["proj"], 1)},
                    {"Team": b, "Record": rec_(c["rec_b"]), "ESPN": c["B"]["espn"], "History": round(c["B"]["hist"], 1), "Projected": round(c["B"]["proj"], 1)},
                ]), hide_index=True, width="stretch")
                for t in c["story"]:
                    st.markdown(f"- {t}")
    elif R and any(r["type"] == "final" for r in R["rows"]):
        st.info("The season is over. The preview returns once next season's schedule is in.")


MODEL_BLURB = ("Projected scores are 70% ESPN's projection for each starting lineup and 30% the team's own scoring "
               "(this season, leaning on a toned-down last season early on). Head-to-head history and win-loss record "
               "are shown for context but don't move the weekly odds. Scores swing about {sd} points either way.")


def page_book():
    s = L.current_season()
    bets = db.load_bets(ENG)
    bankroll, top_up = int(SET.get("bankroll") or 1000), int(SET.get("top_up") or 0)
    led = L.ledger(s, bets, bankroll, top_up)
    wm = L.week_markets()
    sm, sim = get_season(VERSION, REN_KEY, int(SET.get("playoff_teams") or 0))
    st.header("Sportsbook")
    st.caption(f"Play money. Everyone starts {s} with {money(bankroll)}"
               + (f" and gets {money(top_up)} more each new week" if top_up else "")
               + ". Lines come from the league model. Bet on any matchup; you can't bet against your own team.")
    me = ss.team
    if me:
        r = led.get(me)
        if r:
            c = st.columns(4)
            c[0].metric("Balance", money(r["balance"]), f"incl. {money(r['bonus'])} top-ups" if r["bonus"] else None, delta_color="off")
            c[1].metric("Available", money(r["available"]))
            c[2].metric("In play", money(r["risk"] + r["pending"]))
            c[3].metric("Record", record(r["w"], r["l"], r["p"]))
    else:
        st.info("Sign in (sidebar) to bet.")

    tabs = st.tabs([f"Week {wm['week']}" if wm else "Weekly", "Season", "My bets", "Leaderboard", "How lines work"])

    def place(mk, market, side, stake):
        if not me:
            st.error("Sign in first."); return
        season_market = mk.endswith("|season")
        if season_market:
            if not sm or (sim is None):
                st.error("Season markets are closed."); return
        elif is_locked(int(mk.split("|")[0]), int(mk.split("|")[1])):
            st.error("Betting on this week is locked."); return
        err = against_self(me, market["id"], side["id"], side.get("team"), bool(SET.get("allow_self")))
        if err:
            st.error(err); return
        fresh = L.ledger(s, db.load_bets(ENG), bankroll, top_up)[me]
        if stake < 1:
            st.error("Enter a stake."); return
        if stake > fresh["available"]:
            st.error(f"You only have {money(fresh['available'])} available."); return
        status = "pending" if SET.get("require_approval") else "open"
        db.add_bet(ENG, dict(team_name=me, market_key=mk, market_id=market["id"], side=side["id"], team=side.get("team"),
                             line=side.get("line"), price=int(side["price"]), stake=int(stake), status=status))
        st.success(("Bet sent for approval: " if status == "pending" else "Bet placed: ") +
                   f"{market_label(dict(market_id=market['id'], side=side['id'], team=side.get('team'), line=side.get('line')))} "
                   f"at {fmt_odds(side['price'])}, {money(stake)} to win {money(profit(stake, side['price']))}.")

    def bet_form(key, mk, options, disabled):
        """options: list of (label, market, side)."""
        with st.form(key, clear_on_submit=False):
            pick = st.selectbox("Bet", range(len(options)), format_func=lambda i: options[i][0], disabled=disabled)
            stake = st.number_input("Stake ($)", min_value=0, step=10, value=0, disabled=disabled)
            go = st.form_submit_button("Place bet", type="primary", disabled=disabled or not me)
            if pick is not None:
                st.caption(f"A {money(stake)} bet at {fmt_odds(options[pick][2]['price'])} wins {money(profit(stake, options[pick][2]['price']))}.")
            if go:
                _, market, side = options[pick]
                place(mk, market, side, int(stake))

    with tabs[0]:
        if not wm:
            st.info("No upcoming matchups in the data yet.")
        else:
            locked = is_locked(wm["season"], wm["week"])
            st.caption("Locked." if locked else f"Betting closes {fmt_lock(week_lock(wm['season'], wm['week']))}.")
            by_game = {}
            for m in wm["markets"]:
                by_game.setdefault(m["id"].split("|", 1)[1], {})[m["kind"]] = m
            board = []
            for k, g in by_game.items():
                for i in (0, 1):
                    sd_, ml, tt = g["sp"]["sides"][i], g["ml"]["sides"][i], g["tot"]["sides"][i]
                    board.append({"Game": g["ml"]["title"] if i == 0 else "", "Team": ml["label"],
                                  "Spread": f"{fmt_spread(sd_['line'])} ({fmt_odds(sd_['price'])})",
                                  "Moneyline": fmt_odds(ml["price"]),
                                  "Total": f"{'O' if i == 0 else 'U'} {tt['line']:.1f} ({fmt_odds(tt['price'])})"})
            st.dataframe(pd.DataFrame(board), hide_index=True, width="stretch")
            opts = []
            for k, g in by_game.items():
                for kind in ("sp", "ml", "tot"):
                    m = g[kind]
                    for sd_ in m["sides"]:
                        lab = (f"{sd_['label']} {fmt_spread(sd_['line'])}" if kind == "sp" else
                               f"{sd_['label']} to win" if kind == "ml" else f"{sd_['label']} {sd_['line']:.1f} ({m['title']})")
                        if kind != "tot":
                            lab += f" ({m['title']})"
                        if me and against_self(me, m["id"], sd_["id"], sd_.get("team"), bool(SET.get("allow_self"))):
                            continue
                        opts.append((f"{lab}  {fmt_odds(sd_['price'])}", m, sd_))
            bet_form("wk_bet", wm["mk"], opts, locked)

    with tabs[1]:
        if not sm:
            info = L.season_info()
            st.info("Season markets are closed once the playoffs start." if info["started"] or info["done"]
                    else "Season odds appear once the full schedule is loaded.")
        else:
            st.subheader("Title race")
            st.caption(f"4,000 simulated seasons from the current standings, with a {sim['P']}-team playoff"
                       + (f" and {sim['byes']} bye{'s' if sim['byes'] > 1 else ''}" if sim["byes"] else "") + ". "
                       + ("Roster is ESPN's projection for each team's best weekly lineup. " if sim["used_roster"] else
                          "Roster projections aren't loaded yet, so strength comes from scoring history. ")
                       + "Odds before the book's margin.")
            st.dataframe(pd.DataFrame([{"Manager": r["m"], "Record": r["record"],
                                        "Roster": round(r["roster"], 1) if r["roster"] else None,
                                        "History": round(r["hist"], 1), "Proj. wins": round(r["wins"], 1),
                                        "Playoffs": pc(r["po"]), "Bye": pc(r["bye"]), "Final": pc(r["final"]),
                                        "Title": pc(r["title"])} for r in sim["race"]]),
                         hide_index=True, width="stretch")
            mk = {m["id"]: m for m in sm["markets"]}
            c1, c2 = st.columns(2)
            for col, kind in ((c1, "title"), (c2, "pf")):
                m = mk[f"{kind}|{s}"]
                col.markdown(f"**{m['name']}**")
                col.dataframe(pd.DataFrame([{"Manager": x["label"], "Odds": fmt_odds(x["price"])} for x in m["sides"]]),
                              hide_index=True, width="stretch")
            st.markdown("**Win totals and playoffs**")
            st.dataframe(pd.DataFrame([{
                "Manager": r["m"],
                "Wins": f"O/U {mk[f'wins|{s}|{r['m']}']['sides'][0]['line']:.1f}",
                "Over": fmt_odds(mk[f"wins|{s}|{r['m']}"]["sides"][0]["price"]),
                "Under": fmt_odds(mk[f"wins|{s}|{r['m']}"]["sides"][1]["price"]),
                "Make playoffs": fmt_odds(mk[f"po|{s}|{r['m']}"]["sides"][0]["price"]),
                "Miss playoffs": fmt_odds(mk[f"po|{s}|{r['m']}"]["sides"][1]["price"])}
                for r in sorted(sim["race"], key=lambda r: r["m"].lower())]), hide_index=True, width="stretch")
            opts = []
            for m in sm["markets"]:
                for sd_ in m["sides"]:
                    if me and against_self(me, m["id"], sd_["id"], sd_.get("team"), bool(SET.get("allow_self"))):
                        continue
                    lab = market_label(dict(market_id=m["id"], side=sd_["id"], team=sd_.get("team"), line=sd_.get("line")))
                    opts.append((f"{lab}  {fmt_odds(sd_['price'])}", m, sd_))
            bet_form("season_bet", sm["mk"], opts, False)

    with tabs[2]:
        mine = [b for b in bets if b["team_name"] == me and int(b["market_key"].split("|")[0]) == s] if me else []
        if not me:
            st.info("Sign in to see your bets.")
        elif not mine:
            st.info("No bets yet.")
        else:
            st.dataframe(pd.DataFrame([bet_row(b) for b in reversed(mine)]), hide_index=True, width="stretch")

    with tabs[3]:
        board = sorted([r for r in led.values() if r["bets"] or r["pending"]], key=lambda r: -r["balance"])
        if not board:
            st.info("No bets yet this season.")
        else:
            st.dataframe(pd.DataFrame([{"#": i + 1, "Bettor": r["m"], "Balance": money(r["balance"]),
                                        "Profit": ("+" if r["pl"] > 0 else "") + money(r["pl"]),
                                        "Record": record(r["w"], r["l"], r["p"]), "In play": money(r["risk"] + r["pending"])}
                                       for i, r in enumerate(board)]), hide_index=True, width="stretch")
            house = -sum(r["pl"] for r in led.values())
            st.caption(f"Profit counts betting results only. The book is {'up' if house >= 0 else 'down'} {money(abs(house))} this season.")

    with tabs[4]:
        st.markdown(MODEL_BLURB.format(sd=round(L.score_sd())))
        st.markdown("- **Moneyline:** win chance from the two projections, with a normal sportsbook margin (about 4.5%).\n"
                    "- **Spread and total:** projected difference and sum, rounded to the half point, at −110 both ways.\n"
                    "- **Season markets:** team strength is 65% ESPN's season projection for each team's best lineup and 35% its own scoring, "
                    "toned down because rosters change. Starting from the real standings (record, then points), the rest of the season and "
                    "the playoffs are simulated 4,000 times, with extra uncertainty early on. Futures carry a bigger margin.\n"
                    "- **Updates:** lines refresh when new ESPN data is pulled. A bet always keeps the odds it was placed at.\n"
                    "- **Locks:** each week locks Thursday night; the server clock decides.")


def bet_row(b, who=False):
    gr = L.grade(b) if b["status"] == "open" else None
    res = {"pending": "Awaiting approval", "rejected": "Rejected"}.get(b["status"]) or (
        "Open" if not gr else f"Won {money(profit(b['stake'], b['price']))}" if gr == "win" else
        f"Lost {money(b['stake'])}" if gr == "loss" else gr.title())
    wk = b["market_key"].split("|")[1]
    row = {"Bet": market_label(b), "When": "Season" if wk == "season" else f"Week {wk}",
           "Odds": fmt_odds(b["price"]), "Stake": money(b["stake"]), "Result": res}
    return ({"Bettor": b["team_name"]} | row) if who else row


def page_standings():
    inc = st.toggle("Include playoffs", key="scope_st")
    rows_ = L.manager_stats(inc)
    df = pd.DataFrame([{
        "Manager": r["m"], "Seasons": r["seasons"], "Record": record(r["w"], r["l"], r["t"]), "Win %": round(r["winpct"], 3),
        "Pts/game": round(r["ppg"], 1), "Against/game": round(r["papg"], 1), "Margin": round(r["margin"], 1),
        "Best": round(r["hi"], 1), "Worst": round(r["lo"], 1), "Playoff yrs": r["playoff_years"],
        "Playoff W–L": record(r["pw"], r["pl"]), "Titles": r["titles"], "All-play %": round(r["ap"], 3),
        "Luck": round(r["luck"], 1)} for r in rows_]).sort_values("Win %", ascending=False)
    st.header("All-time standings")
    st.caption("All-play: your record if you played every team every week. Luck: actual regular-season wins minus the wins "
               "your all-play record predicts. Click a column to sort.")
    st.dataframe(df, hide_index=True, width="stretch")


def page_h2h():
    inc = st.toggle("Include playoffs", key="scope_h2h")
    M = L.h2h(inc)
    ms = [m for m in L.managers if m in M]
    grid, shade = [], []
    for a in ms:
        row, srow = {"": a}, {"": None}
        for b in ms:
            r = M[a].get(b)
            if a == b or not r:
                row[b], srow[b] = "", None
            else:
                n = r["w"] + r["l"] + r["t"]
                row[b], srow[b] = record(r["w"], r["l"], r["t"]), (r["w"] + r["t"] / 2) / n
        grid.append(row); shade.append(srow)
    df, sh = pd.DataFrame(grid).set_index(""), pd.DataFrame(shade).set_index("")

    def color(col):
        out = []
        for v in sh[col.name]:
            if v is None or pd.isna(v): out.append("")
            elif v > 0.5: out.append(f"background-color: rgba(47,107,79,{min(0.55, (v - .5) * 1.1):.2f})")
            elif v < 0.5: out.append(f"background-color: rgba(168,38,29,{min(0.55, (.5 - v) * 1.1):.2f})")
            else: out.append("")
        return out

    st.header("Head to head")
    st.caption("Row manager's record against the column manager.")
    st.dataframe(df.style.apply(color), width="stretch")
    c1, c2 = st.columns(2)
    a = c1.selectbox("Manager", ms, key="h2h_a")
    b = c2.selectbox("Opponent", [m for m in ms if m != a], key="h2h_b")
    r = M[a].get(b)
    if r:
        n = len(r["games"])
        k = st.columns(3)
        k[0].metric("Series", record(r["w"], r["l"], r["t"]))
        k[1].metric("Average score", f"{r['pf'] / n:.1f} – {r['pa'] / n:.1f}")
        k[2].metric(f"{a}'s last 5", " ".join("W" if x["pf"] > x["pa"] else "L" if x["pf"] < x["pa"] else "T" for x in r["games"][-5:]))
        st.dataframe(pd.DataFrame([{"Season": x["g"]["season"], "Week": x["g"]["week"], "Game": x["g"]["type"].title(),
                                    a: round(x["pf"], 2), b: round(x["pa"], 2),
                                    "Result": "W" if x["pf"] > x["pa"] else "L" if x["pf"] < x["pa"] else "T",
                                    "Margin": round(x["pf"] - x["pa"], 2)} for x in reversed(r["games"])]),
                     hide_index=True, width="stretch")


def page_managers():
    inc = st.toggle("Include playoffs", key="scope_mg")
    stats = {r["m"]: r for r in L.manager_stats(inc)}
    default = max(stats.values(), key=lambda r: (r["titles"], r["winpct"]))["m"]
    m = st.selectbox("Manager", L.managers, index=L.managers.index(ss.team if ss.team in L.managers else default))
    r = stats[m]
    st_ = L.streaks(inc).get(m, {})
    c = st.columns(6)
    c[0].metric("Record", record(r["w"], r["l"], r["t"]))
    c[1].metric("Points per game", f"{r['ppg']:.1f}")
    c[2].metric("Titles", r["titles"], f"{r['finals']} finals", delta_color="off")
    c[3].metric("Playoff seasons", f"{r['playoff_years']} of {r['seasons']}")
    c[4].metric("Playoff record", record(r["pw"], r["pl"]))
    c[5].metric("Longest streaks", f"{st_.get('best_win', {}).get('n', 0)}W / {st_.get('best_loss', {}).get('n', 0)}L")
    ppg = []
    for s in L.seasons:
        t = L.season_tables[s]
        row = L.table_row(s, m)
        lg = sum(x["pf"] for x in t) / sum(x["gp"] for x in t)
        ppg.append({"Season": str(s), "Series": "League average", "Points per game": round(lg, 1)})
        if row:
            ppg.append({"Season": str(s), "Series": m, "Points per game": round(row["pf"] / row["gp"], 1)})
    st.altair_chart(alt.Chart(pd.DataFrame(ppg)).mark_line(point=True).encode(
        x="Season", y=alt.Y("Points per game", scale=alt.Scale(zero=False)), color="Series"), width="stretch")
    seasons_ = []
    for s in L.seasons:
        row = L.table_row(s, m)
        if not row: continue
        res = "Champion" if L.champs.get(s) == m else "Runner-up" if L.runners.get(s) == m else "Playoffs" if (m, s) in L.post_seasons else ""
        seasons_.append({"Season": s, "Finish": f"{row['rank']} of {len(L.season_tables[s])}", "Record": record(row["w"], row["l"], row["t"]),
                         "Points for": round(row["pf"], 1), "Against": round(row["pa"], 1), "Luck": round(row["luck"], 1), "Result": res})
    st.subheader("Season by season")
    st.dataframe(pd.DataFrame(seasons_), hide_index=True, width="stretch")
    H = L.h2h(inc).get(m, {})
    st.subheader("Against each opponent")
    st.dataframe(pd.DataFrame([{"Opponent": o, "Record": record(x["w"], x["l"], x["t"]),
                                "Win %": round((x["w"] + x["t"] / 2) / len(x["games"]), 3),
                                "Scored/game": round(x["pf"] / len(x["games"]), 1), "Allowed/game": round(x["pa"] / len(x["games"]), 1)}
                               for o, x in H.items()]).sort_values("Win %", ascending=False), hide_index=True, width="stretch")


def page_records():
    inc = st.toggle("Include playoffs", key="scope_rec")
    f = L.scope(inc)
    games = [g for g in L.games if f(g)]
    sides_ = [x for g in games for x in ({"m": g["a"], "o": g["b"], "pf": g["sa"], "pa": g["sb"], "g": g},
                                         {"m": g["b"], "o": g["a"], "pf": g["sb"], "pa": g["sa"], "g": g})]
    when = lambda g: f"{g['season']} wk {g['week']}"
    side_df = lambda xs: pd.DataFrame([{"Manager": x["m"], "Score": round(x["pf"], 2), "Opponent": f"{x['o']} ({x['pa']:.2f})", "When": when(x["g"])} for x in xs[:5]])
    game_df = lambda gs, val: pd.DataFrame([{"Winner": g["a"] if g["sa"] >= g["sb"] else g["b"], "Value": round(val(g), 2),
                                             "Score": f"{max(g['sa'], g['sb']):.2f} – {min(g['sa'], g['sb']):.2f}",
                                             "Loser": g["b"] if g["sa"] >= g["sb"] else g["a"], "When": when(g)} for g in gs[:5]])
    st.header("Record book")
    blocks = [
        ("Highest scores", side_df(sorted(sides_, key=lambda x: -x["pf"]))),
        ("Lowest scores", side_df(sorted(sides_, key=lambda x: x["pf"]))),
        ("Biggest blowouts", game_df(sorted(games, key=lambda g: -abs(g["sa"] - g["sb"])), lambda g: abs(g["sa"] - g["sb"]))),
        ("Closest games", game_df(sorted(games, key=lambda g: abs(g["sa"] - g["sb"])), lambda g: abs(g["sa"] - g["sb"]))),
        ("Most points in a loss", side_df(sorted([x for x in sides_ if x["pf"] < x["pa"]], key=lambda x: -x["pf"]))),
        ("Fewest points in a win", side_df(sorted([x for x in sides_ if x["pf"] > x["pa"]], key=lambda x: x["pf"]))),
        ("Highest combined", game_df(sorted(games, key=lambda g: -(g["sa"] + g["sb"])), lambda g: g["sa"] + g["sb"])),
        ("Lowest combined", game_df(sorted(games, key=lambda g: g["sa"] + g["sb"]), lambda g: g["sa"] + g["sb"])),
    ]
    stks = L.streaks(inc)
    span = lambda x: f"{x['start']['season']} wk {x['start']['week']} to {x['end']['season']} wk {x['end']['week']}" if x.get("start") else ""
    blocks.append(("Longest winning streaks", pd.DataFrame([{"Manager": m, "Games": v["best_win"]["n"], "When": span(v["best_win"])}
                                                           for m, v in sorted(stks.items(), key=lambda kv: -kv[1]["best_win"]["n"])[:5]])))
    blocks.append(("Longest losing streaks", pd.DataFrame([{"Manager": m, "Games": v["best_loss"]["n"], "When": span(v["best_loss"])}
                                                          for m, v in sorted(stks.items(), key=lambda kv: -kv[1]["best_loss"]["n"])[:5]])))
    seas = [(s, r) for s in L.seasons for r in L.season_tables[s]]
    blocks.append(("Most points in a season", pd.DataFrame([{"Manager": r["m"], "Season": s, "Points": round(r["pf"], 1), "Per game": round(r["pf"] / r["gp"], 1)}
                                                           for s, r in sorted(seas, key=lambda x: -x[1]["pf"] / x[1]["gp"])[:5]])))
    blocks.append(("Best records", pd.DataFrame([{"Manager": r["m"], "Season": s, "Record": record(r["w"], r["l"], r["t"]), "Title": "Yes" if L.champs.get(s) == r["m"] else ""}
                                                 for s, r in sorted(seas, key=lambda x: (-x[1]["winpct"], -x[1]["pf"]))[:5]])))
    blocks.append(("Unluckiest seasons", pd.DataFrame([{"Manager": r["m"], "Season": s, "Luck": round(r["luck"], 1), "Record": record(r["w"], r["l"], r["t"]), "All-play": round(r["ap"], 3)}
                                                       for s, r in sorted(seas, key=lambda x: x[1]["luck"])[:5]])))
    blocks.append(("Luckiest seasons", pd.DataFrame([{"Manager": r["m"], "Season": s, "Luck": round(r["luck"], 1), "Record": record(r["w"], r["l"], r["t"]), "All-play": round(r["ap"], 3)}
                                                     for s, r in sorted(seas, key=lambda x: -x[1]["luck"])[:5]])))
    cols = st.columns(2)
    for i, (title, df) in enumerate(blocks):
        with cols[i % 2]:
            st.subheader(title)
            st.dataframe(df, hide_index=True, width="stretch")


def page_trends():
    st.header("Trends")
    st.caption("Regular-season games only.")
    rows_ = []
    for s in L.seasons:
        t = L.season_tables[s]
        rows_ += [{"Season": str(s), "Series": "League average", "Points": round(sum(x["pf"] for x in t) / sum(x["gp"] for x in t), 1)},
                  {"Season": str(s), "Series": "Best team", "Points": round(max(x["pf"] / x["gp"] for x in t), 1)},
                  {"Season": str(s), "Series": "Worst team", "Points": round(min(x["pf"] / x["gp"] for x in t), 1)}]
    st.subheader("League scoring by season")
    st.altair_chart(alt.Chart(pd.DataFrame(rows_)).mark_line(point=True).encode(
        x="Season", y=alt.Y("Points", scale=alt.Scale(zero=False)), color="Series"), width="stretch")
    stats = sorted(L.manager_stats(), key=lambda r: -r["winpct"])
    picks = st.multiselect("Compare managers", L.managers, default=[r["m"] for r in stats[:3]])
    data = [{"Season": str(s), "Manager": r["m"], "Points per game": round(r["pf"] / r["gp"], 1), "Finish": r["rank"]}
            for s in L.seasons for r in L.season_tables[s] if r["m"] in picks]
    if data:
        c1, c2 = st.columns(2)
        base = alt.Chart(pd.DataFrame(data)).encode(x="Season", color="Manager")
        c1.altair_chart(base.mark_line(point=True).encode(y=alt.Y("Points per game", scale=alt.Scale(zero=False))), width="stretch")
        c2.altair_chart(base.mark_line(point=True).encode(y=alt.Y("Finish", scale=alt.Scale(reverse=True, domainMin=1))), width="stretch")
    st.subheader("Finish by season")
    grid = []
    for m in L.managers:
        row = {"Manager": m}
        ranks = []
        for s in L.seasons:
            r = L.table_row(s, m)
            row[str(s)] = (f"{r['rank']}" + (" 🏆" if L.champs.get(s) == m else "")) if r else ""
            if r: ranks.append(r["rank"])
        row["Avg finish"] = round(sum(ranks) / len(ranks), 1) if ranks else None
        grid.append(row)
    st.dataframe(pd.DataFrame(grid).sort_values("Avg finish"), hide_index=True, width="stretch")


def page_seasons():
    s = st.selectbox("Season", list(reversed(L.seasons)))
    t = L.season_tables[s]
    c = st.columns(3)
    c[0].metric("Champion", L.champs.get(s, "—"))
    c[1].metric("League average", f"{sum(x['pf'] for x in t) / sum(x['gp'] for x in t):.1f}")
    hi = max(((x, g) for g in L.games if g["season"] == s and g["type"] != "consolation" for x in (g["a"], g["b"])),
             key=lambda xg: xg[1]["sa"] if xg[0] == xg[1]["a"] else xg[1]["sb"])
    c[2].metric("High score", f"{hi[1]['sa'] if hi[0] == hi[1]['a'] else hi[1]['sb']:.2f}", f"{hi[0]}, week {hi[1]['week']}", delta_color="off")
    st.subheader("Regular-season standings")
    st.dataframe(pd.DataFrame([{"#": r["rank"], "Manager": r["m"] + (" 🏆" if L.champs.get(s) == r["m"] else ""),
                                "Record": record(r["w"], r["l"], r["t"]), "Points for": round(r["pf"], 1), "Against": round(r["pa"], 1),
                                "All-play %": round(r["ap"], 3), "Luck": round(r["luck"], 1)} for r in t]),
                 hide_index=True, width="stretch")
    post = [g for g in L.games if g["season"] == s and g["type"] != "regular"]
    if post:
        st.subheader("Postseason")
        st.dataframe(pd.DataFrame([{"Week": g["week"], "Game": {"final": "Title game", "playoff": "Playoffs"}.get(g["type"], "Consolation"),
                                    "Winner": g["a"] if g["sa"] >= g["sb"] else g["b"],
                                    "Loser": g["b"] if g["sa"] >= g["sb"] else g["a"],
                                    "Score": f"{max(g['sa'], g['sb']):.2f} – {min(g['sa'], g['sb']):.2f}"} for g in post]),
                     hide_index=True, width="stretch")


def page_admin():
    if not ss.admin:
        st.error("Unlock commissioner tools in the sidebar."); return
    st.header("Commissioner")
    s = L.current_season()

    st.subheader("Data")
    lr = db.last_refresh(ENG)
    st.caption("Last update: " + (fmt_dt(datetime.fromtimestamp(lr["at"] / 1000)) + f" ({lr['note']})" if lr else "never"))
    c1, c2 = st.columns(2)
    with c1:
        if secret("ESPN_LEAGUE_ID"):
            if st.button(f"Pull {s} from ESPN now", type="primary"):
                for k in ("ESPN_LEAGUE_ID", "ESPN_S2", "ESPN_SWID"):
                    if secret(k): os.environ[k] = str(secret(k))
                from core.espn_export import espn
                with st.spinner("Pulling from ESPN…"):
                    try:
                        export = espn(str(secret("ESPN_LEAGUE_ID")), s, s, secret("ESPN_S2"), secret("ESPN_SWID"))
                        seasons, n, k = db.replace_seasons(ENG, export, note=f"manual pull {s}")
                        teams = {x for r in export if r[6] != "strength" for x in (r[2], r[4])}
                        ren = db.load_renames(ENG)
                        db.ensure_accounts(ENG, {ren.get(t, t) for t in teams if t})
                        clear_caches()
                        st.success(f"Saved {n} game rows and {k} roster projections for {seasons}.")
                    except SystemExit as e:
                        st.error(f"ESPN refused the request: {e}")
                    except Exception as e:
                        st.error(f"Pull failed: {e}")
        else:
            st.caption("Add ESPN_LEAGUE_ID, ESPN_S2 and ESPN_SWID to the app's secrets to pull from here.")
    with c2:
        up = st.file_uploader("Or upload a league_games.csv export", type="csv")
        if up is not None and st.button("Load this file"):
            import csv, io
            rdr = csv.DictReader(io.StringIO(up.getvalue().decode("utf-8")))
            export = [[r["season"], r["week"], r["manager_a"], r["score_a"], r["manager_b"], r["score_b"], r.get("type") or "regular",
                       r.get("proj_a", ""), r.get("proj_b", "")] for r in rdr]
            seasons, n, k = db.replace_seasons(ENG, export, note=f"upload {up.name}")
            ren = db.load_renames(ENG)
            db.ensure_accounts(ENG, {ren.get(x, x) for r in export if r[6] != "strength" for x in (r[2], r[4]) if x})
            clear_caches()
            st.success(f"Loaded seasons {seasons[0]}–{seasons[-1]} ({n} rows).")

    st.subheader("Settings")
    with st.form("settings"):
        c = st.columns(3)
        name = c[0].text_input("League name", SET.get("league_name", ""))
        bankroll = c[1].number_input("Starting bankroll", min_value=1, value=int(SET.get("bankroll") or 1000), step=50)
        top_up = c[2].number_input("Weekly top-up", min_value=0, value=int(SET.get("top_up") or 0), step=25)
        c = st.columns(3)
        playoff = c[0].number_input("Playoff teams (0 = from past seasons)", min_value=0, max_value=16, value=int(SET.get("playoff_teams") or 0))
        ko = (SET.get("kickoff") or {}).get(str(s))
        kickoff = c[1].date_input(f"{s} week 1 Thursday", value=datetime.fromisoformat(ko).date() if ko else None)
        lock_at = c[2].text_input("Weekly lock time, Eastern (HH:MM)", SET.get("lock_at", "20:15"))
        allow_self = st.checkbox("Allow betting against your own team", bool(SET.get("allow_self")))
        approval = st.checkbox("Bets need my approval", bool(SET.get("require_approval")))
        if st.form_submit_button("Save settings", type="primary"):
            db.set_setting(ENG, "league_name", name)
            db.set_setting(ENG, "bankroll", int(bankroll))
            db.set_setting(ENG, "top_up", int(top_up))
            db.set_setting(ENG, "playoff_teams", int(playoff))
            kk = dict(SET.get("kickoff") or {})
            if kickoff: kk[str(s)] = kickoff.isoformat()
            db.set_setting(ENG, "kickoff", kk)
            db.set_setting(ENG, "lock_at", lock_at)
            db.set_setting(ENG, "allow_self", bool(allow_self))
            db.set_setting(ENG, "require_approval", bool(approval))
            st.success("Saved.")
            st.rerun()
    nw = L.next_week()
    if nw and not is_locked(nw[0], nw[1]) and st.button(f"Lock week {nw[1]} now"):
        locks = dict(SET.get("manual_locks") or {})
        locks[f"{nw[0]}|{nw[1]}"] = True
        db.set_setting(ENG, "manual_locks", locks)
        st.rerun()

    bets = db.load_bets(ENG)
    pend = [b for b in bets if b["status"] == "pending"]
    st.subheader("Waiting for approval")
    if not pend:
        st.caption("Nothing waiting.")
    for b in pend:
        c = st.columns([5, 1, 1])
        c[0].write(f"**{b['team_name']}**: {market_label(b)} {fmt_odds(b['price'])}, {money(b['stake'])}")
        if c[1].button("Accept", key=f"acc{b['id']}"):
            db.update_bet(ENG, b["id"], status="open"); st.rerun()
        if c[2].button("Reject", key=f"rej{b['id']}"):
            db.update_bet(ENG, b["id"], status="rejected"); st.rerun()

    st.subheader("Bets this season")
    season_bets = [b for b in bets if int(b["market_key"].split("|")[0]) == s and b["status"] == "open"]
    if season_bets:
        df = pd.DataFrame([dict(bet_row(b, who=True), id=b["id"], Override=b["grade"] or "auto") for b in reversed(season_bets)])
        edited = st.data_editor(df, hide_index=True, width="stretch", disabled=[c for c in df.columns if c != "Override"],
                                column_config={"id": None, "Override": st.column_config.SelectboxColumn(
                                    options=["auto", "win", "loss", "push", "void"])})
        if st.button("Save grade overrides"):
            for _, row in edited.iterrows():
                orig = next(b for b in season_bets if b["id"] == row["id"])
                new = None if row["Override"] == "auto" else row["Override"]
                if new != orig["grade"]:
                    db.update_bet(ENG, row["id"], grade=new)
            st.success("Saved."); st.rerun()
    else:
        st.caption("No bets yet.")

    st.subheader("Passwords")
    have = db.teams_with_accounts(ENG)
    st.caption("New teams get their team name as the password. Anyone can change their own in the sidebar.")
    with st.form("pw_admin", clear_on_submit=True):
        c = st.columns(2)
        t = c[0].selectbox("Team", L.managers)
        p = c[1].text_input("New password (blank = team name)")
        if st.form_submit_button("Set password"):
            db.set_password(ENG, t, p or t)
            st.success(f"Password set for {t}.")
    missing = [m for m in L.managers if m not in have]
    if missing:
        st.warning("No password yet: " + ", ".join(missing))

    st.subheader("Names")
    st.caption("Show an ESPN name differently everywhere (for example co-managers).")
    ren = db.load_renames(ENG)
    with st.form("rename"):
        c = st.columns(2)
        espn_name = c[0].text_input("Name as ESPN has it")
        disp = c[1].text_input("Name to show (blank removes)")
        if st.form_submit_button("Save name"):
            db.set_rename(ENG, espn_name.strip(), disp.strip())
            if disp.strip():
                old_accounts = db.rows(ENG, "SELECT * FROM accounts WHERE team = :t", t=espn_name.strip())
                if old_accounts and disp.strip() not in db.teams_with_accounts(ENG):
                    db.set_password(ENG, disp.strip(), disp.strip())
            clear_caches()
            st.rerun()
    if ren:
        st.dataframe(pd.DataFrame([{"ESPN name": k, "Shown as": v} for k, v in ren.items()]), hide_index=True)


PAGES = {"This week": page_week, "Sportsbook": page_book, "All-time standings": page_standings, "Head to head": page_h2h,
         "Managers": page_managers, "Record book": page_records, "Trends": page_trends, "Seasons": page_seasons,
         "Commissioner": page_admin}
if L.games:
    PAGES[page]()
elif page == "Commissioner":
    page_admin()
