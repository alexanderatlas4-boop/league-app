#!/usr/bin/env python3
"""
Export fantasy football league history into the record book CSV.

    season,week,manager_a,score_a,manager_b,score_b,type,proj_a,proj_b

proj_a/proj_b are ESPN's projected points for next week's matchups (ESPN only; blank otherwise).
Rows typed "strength" hold each team's best projected weekly lineup for the season (ESPN only).

Only uses the Python standard library. Run it on your own computer.

SLEEPER (pass the league ID for the MOST RECENT season; it walks back through
previous seasons automatically):
    python league_export.py sleeper 1048xxxxxxxxxxxxxxx

ESPN (public league):
    python league_export.py espn 12345678 --start 2017 --end 2025

ESPN (private league): copy the espn_s2 and SWID cookies from your browser
while logged in to fantasy.espn.com (DevTools > Application > Cookies):
    python league_export.py espn 12345678 --start 2017 --end 2025 \
        --espn-s2 "AEB..." --swid "{ABCD-...}"

Renaming / merging managers (someone changed their display name, or you are
combining years from two platforms): make a two-column CSV `old,new` and pass
--rename names.csv.

Combining platforms: export each one to its own file, then paste the rows of
both into one CSV (keep a single header row) before importing.
"""
import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "league-record-book/1.0"}


class AccessDenied(Exception):
    pass


def get_json(url, cookies=None, extra_headers=None):
    headers = dict(UA)
    headers.update(extra_headers or {})
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AccessDenied(e.code)
            if e.code == 404:
                return None
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(1.5 * (attempt + 1))
    return None


# ---------------------------------------------------------------- Sleeper
def sleeper(league_id):
    base = "https://api.sleeper.app/v1"
    leagues = []
    lid = league_id
    while lid and lid != "0":
        info = get_json(f"{base}/league/{lid}")
        if not info:
            break
        leagues.append(info)
        lid = info.get("previous_league_id")
    if not leagues:
        raise SystemExit(f"Sleeper league {league_id} not found.")

    # user_id is stable across seasons; use the newest display name for each person.
    names = {}
    per_league = []
    for info in leagues:  # newest first
        lid = info["league_id"]
        users = get_json(f"{base}/league/{lid}/users") or []
        for u in users:
            names.setdefault(u["user_id"], u.get("display_name") or u["user_id"])
        rosters = get_json(f"{base}/league/{lid}/rosters") or []
        owner_of = {r["roster_id"]: r.get("owner_id") for r in rosters}
        per_league.append((info, owner_of))

    state = get_json(f"{base}/state/nfl") or {}
    rows = []
    for info, owner_of in reversed(per_league):  # oldest first
        lid, season = info["league_id"], int(info["season"])
        st = info.get("settings", {})
        pws = int(st.get("playoff_week_start") or 15)
        round_type = int(st.get("playoff_round_type") or 0)  # 0: 1 wk/round, 1: 2-wk final, 2: 2 wks/round
        if info.get("status") not in ("complete", "in_season", "post_season"):
            print(f"  {season}: status {info.get('status')}, skipping", file=sys.stderr)
            continue

        def who(rid):
            uid = owner_of.get(rid)
            return names.get(uid, f"Roster {rid}") if uid else f"Roster {rid}"

        week_points = {}

        def points(week):
            if week not in week_points:
                data = get_json(f"{base}/league/{lid}/matchups/{week}") or []
                week_points[week] = data
            return week_points[week]

        live = str(state.get("season")) == str(season) and info.get("status") != "complete"
        current_week = int(state.get("week") or 0) if live else None

        # Regular season
        for week in range(1, pws):
            games = {}
            for m in points(week):
                if m.get("matchup_id") is None:
                    continue
                games.setdefault(m["matchup_id"], []).append(m)
            for pair in games.values():
                if len(pair) != 2:
                    continue
                a, b = pair
                if current_week is not None and week >= current_week:
                    rows.append([season, week, who(a["roster_id"]), "", who(b["roster_id"]), "", "scheduled"])
                    continue
                sa, sb = float(a.get("points") or 0), float(b.get("points") or 0)
                if sa == 0 and sb == 0:
                    if current_week is None:
                        continue  # not played
                    rows.append([season, week, who(a["roster_id"]), "", who(b["roster_id"]), "", "scheduled"])
                    continue
                rows.append([season, week, who(a["roster_id"]), round(sa, 2), who(b["roster_id"]), round(sb, 2), "regular"])

        # Playoffs (winners bracket only; consolation brackets are left out)
        bracket = get_json(f"{base}/league/{lid}/winners_bracket") or []
        if not bracket:
            continue
        last_round = max(g["r"] for g in bracket)

        def weeks_for(r):
            if round_type == 2:
                start = pws + 2 * (r - 1)
                return [start, start + 1]
            if round_type == 1 and r == last_round:
                return [pws + r - 1, pws + r]
            return [pws + r - 1]

        # Title game: the one Sleeper marks p=1, else the lowest-numbered last-round game.
        marked = [x for x in bracket if x.get("p") == 1]
        final_m = marked[0]["m"] if marked else min(x["m"] for x in bracket if x["r"] == last_round)

        for g in bracket:
            t1, t2 = g.get("t1"), g.get("t2")
            if not isinstance(t1, int) or not isinstance(t2, int) or g.get("w") is None:
                continue
            place = g.get("p")
            if place not in (None, 1):
                continue  # 3rd/5th place games
            wks = weeks_for(g["r"])
            s1 = sum(float(m.get("points") or 0) for w in wks for m in points(w) if m["roster_id"] == t1)
            s2 = sum(float(m.get("points") or 0) for w in wks for m in points(w) if m["roster_id"] == t2)
            # Keep the bracket's winner as the winner even if a tiebreaker decided it.
            if (s1 > s2 and g["w"] == t2) or (s2 > s1 and g["w"] == t1) or s1 == s2:
                print(f"  {season} round {g['r']}: scores and bracket winner disagree; check this game", file=sys.stderr)
            kind = "final" if g["m"] == final_m else "playoff"
            rows.append([season, wks[-1], who(t1), round(s1, 2), who(t2), round(s2, 2), kind])
        print(f"  {season}: done", file=sys.stderr)
    return rows


# ---------------------------------------------------------------- ESPN
def espn(league_id, start, end, espn_s2=None, swid=None):
    cookies = {"espn_s2": espn_s2, "SWID": swid} if (espn_s2 or swid) else None
    host = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
    views = "view=mMatchupScore&view=mTeam&view=mSettings"
    seasons, denied = [], []
    for year in range(start, end + 1):
        try:
            if year >= 2018:
                data = get_json(f"{host}/seasons/{year}/segments/0/leagues/{league_id}?{views}", cookies)
            else:
                data = get_json(f"{host}/leagueHistory/{league_id}?seasonId={year}&{views}", cookies)
                data = data[0] if isinstance(data, list) and data else None
        except AccessDenied as e:
            print(f"  {year}: access denied ({e}), skipping", file=sys.stderr)
            denied.append(year)
            continue
        if not data or not data.get("schedule"):
            print(f"  {year}: no data, skipping", file=sys.stderr)
            continue
        seasons.append((year, data))

    if not seasons:
        raise SystemExit(
            "ESPN refused every season. The cookies were not accepted.\n"
            "Re-copy espn_s2 and SWID while logged in (value only, SWID with braces) and try again."
        )
    if denied:
        print(f"  Locked seasons: {denied}. Usually this means your account was not in the league those years;"
              " a league member from then can run the script for them.", file=sys.stderr)

    # Member ids are stable across seasons; use the newest name for each person.
    names = {}
    for year, data in reversed(seasons):
        for mbr in data.get("members", []) or []:
            full = f"{mbr.get('firstName', '')} {mbr.get('lastName', '')}".strip()
            names.setdefault(mbr["id"], full or mbr.get("displayName") or mbr["id"])

    rows = []
    for year, data in seasons:
        team_owner = {}
        for t in data.get("teams", []):
            owner = t.get("primaryOwner") or (t.get("owners") or [None])[0]
            fallback = (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Team {t['id']}")
            team_owner[t["id"]] = names.get(owner, fallback) if owner else fallback

        sched = [g for g in data["schedule"] if g.get("away") and g.get("winner") not in (None, "UNDECIDED")]
        wb = [g for g in sched if g.get("playoffTierType") == "WINNERS_BRACKET"]
        final_period = max((g["matchupPeriodId"] for g in wb), default=None)
        for g in sched:
            tier = g.get("playoffTierType", "NONE")
            if tier == "NONE":
                kind = "regular"
            elif tier == "WINNERS_BRACKET":
                kind = "final" if g["matchupPeriodId"] == final_period else "playoff"
            else:
                continue  # consolation ladders
            h, a = g["home"], g["away"]
            rows.append([
                year, g["matchupPeriodId"],
                team_owner.get(h["teamId"], f"Team {h['teamId']}"), round(float(h.get("totalPoints") or 0), 2),
                team_owner.get(a["teamId"], f"Team {a['teamId']}"), round(float(a.get("totalPoints") or 0), 2),
                kind,
            ])
        print(f"  {year}: {len([r for r in rows if r[0] == year])} games", file=sys.stderr)

    # Remaining matchups (newest season only) power the preview, picks and sportsbook.
    year, data = seasons[-1]
    pending = [g for g in data["schedule"]
               if g.get("away") and g.get("winner") in (None, "UNDECIDED")
               and g.get("playoffTierType", "NONE") in ("NONE", "WINNERS_BRACKET")]
    proj, nxt = {}, None
    if pending:
        nxt = min(g["matchupPeriodId"] for g in pending)
        proj = espn_projections(host, league_id, year, nxt, cookies)
        n = sum(1 for g in pending if g["matchupPeriodId"] == nxt)
        got = sum(1 for g in pending if g["matchupPeriodId"] == nxt
                  and g["home"]["teamId"] in proj and g["away"]["teamId"] in proj)
        print(f"  ESPN projections found for {got} of {n} week {nxt} matchups", file=sys.stderr)
    if pending:
        strength = espn_roster_strength(host, league_id, year, cookies)
        print(f"  Season roster projections found for {len(strength)} of {len(team_owner)} teams", file=sys.stderr)
        for tid, pts in strength.items():
            rows.append([year, 0, team_owner.get(tid, f"Team {tid}"), pts, "", "", "strength", "", ""])
    for g in pending:
        h, a = g["home"]["teamId"], g["away"]["teamId"]
        rows.append([year, g["matchupPeriodId"],
                     team_owner.get(h, f"Team {h}"), "",
                     team_owner.get(a, f"Team {a}"), "", "scheduled",
                     proj.get(h, "") if g["matchupPeriodId"] == nxt else "",
                     proj.get(a, "") if g["matchupPeriodId"] == nxt else ""])
    return rows


def espn_projections(host, league_id, year, week, cookies):
    """ESPN's projected points for each team's current lineup in `week`. Returns {teamId: points}.
    Missing or unreadable data just returns what it could find; the dashboard falls back to history."""
    url = (f"{host}/seasons/{year}/segments/0/leagues/{league_id}"
           f"?view=mMatchupScore&view=mScoreboard&scoringPeriodId={week}")
    filt = {"schedule": {"filterMatchupPeriodIds": {"value": [week]}}}
    try:
        data = get_json(url, cookies, {"x-fantasy-filter": json.dumps(filt)})
    except Exception as e:  # never let projections break the export
        print(f"  projections: request failed ({e})", file=sys.stderr)
        return {}
    out = {}
    for g in (data or {}).get("schedule", []):
        if g.get("matchupPeriodId") != week:
            continue
        for side in ("home", "away"):
            t = g.get(side) or {}
            if "teamId" not in t:
                continue
            total, found = 0.0, False
            for e in (t.get("rosterForCurrentScoringPeriod") or {}).get("entries", []):
                if e.get("lineupSlotId") in (20, 21):  # bench, IR
                    continue
                player = ((e.get("playerPoolEntry") or {}).get("player") or {})
                for st in player.get("stats", []) or []:
                    if st.get("statSourceId") == 1 and st.get("scoringPeriodId") == week:
                        total += float(st.get("appliedTotal") or 0)
                        found = True
                        break
            if found:
                out[t["teamId"]] = round(total, 2)
            elif isinstance(t.get("totalProjectedPointsLive"), (int, float)):
                out[t["teamId"]] = round(float(t["totalProjectedPointsLive"]), 2)
    return out


BENCH_SLOTS = {20, 21}          # bench, IR
FLEX_FIRST_LAST = 100           # sort key so specific positions fill before flex spots


def espn_roster_strength(host, league_id, year, cookies):
    """Per-week points of each team's best possible starting lineup, using ESPN's season projections.
    Returns {teamId: points}. Anything unreadable is skipped; the dashboard falls back to history."""
    url = f"{host}/seasons/{year}/segments/0/leagues/{league_id}?view=mRoster&view=mSettings"
    try:
        data = get_json(url, cookies) or {}
    except Exception as e:
        print(f"  roster strength: request failed ({e})", file=sys.stderr)
        return {}
    counts = ((data.get("settings") or {}).get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    slots = []
    for sid, n in counts.items():
        sid = int(sid)
        if sid not in BENCH_SLOTS:
            slots += [sid] * int(n or 0)
    if not slots:
        print("  roster strength: no lineup settings found", file=sys.stderr)
        return {}
    out = {}
    for t in data.get("teams", []):
        players = []
        for e in (t.get("roster") or {}).get("entries", []):
            pl = ((e.get("playerPoolEntry") or {}).get("player") or {})
            per_game = None
            for st in pl.get("stats", []) or []:
                if (st.get("statSourceId") == 1 and st.get("statSplitTypeId") == 0
                        and st.get("seasonId") == year):
                    avg = st.get("appliedAverage")
                    per_game = float(avg) if avg else float(st.get("appliedTotal") or 0) / 17.0
                    break
            if per_game and per_game > 0:
                players.append((per_game, set(pl.get("eligibleSlots") or [])))
        if not players:
            continue
        # Fill the most restrictive slots first with the best eligible player left.
        pool = sorted(players, key=lambda x: -x[0])
        def eligible_count(sid):
            return sum(1 for _, el in pool if sid in el)
        order = sorted(slots, key=eligible_count)
        total, used = 0.0, set()
        for sid in order:
            for i, (pts, el) in enumerate(pool):
                if i not in used and sid in el:
                    used.add(i)
                    total += pts
                    break
        out[t["id"]] = round(total, 2)
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Export league history to the record book CSV.")
    sub = ap.add_subparsers(dest="platform", required=True)
    s = sub.add_parser("sleeper", help="Sleeper league (newest season's league ID)")
    s.add_argument("league_id")
    e = sub.add_parser("espn", help="ESPN league")
    e.add_argument("league_id")
    e.add_argument("--start", type=int, required=True, help="first season, e.g. 2017")
    e.add_argument("--end", type=int, required=True, help="last season, e.g. 2025")
    e.add_argument("--espn-s2", help="espn_s2 cookie (private leagues)")
    e.add_argument("--swid", help="SWID cookie including braces (private leagues)")
    for p in (s, e):
        p.add_argument("--out", default="league_games.csv")
        p.add_argument("--rename", help="CSV of old,new manager names")
    args = ap.parse_args()

    rows = sleeper(args.league_id) if args.platform == "sleeper" else espn(args.league_id, args.start, args.end, args.espn_s2, args.swid)

    if args.rename:
        with open(args.rename, newline="", encoding="utf-8") as f:
            mapping = {r[0].strip(): r[1].strip() for r in csv.reader(f) if len(r) >= 2 and r[0].strip()}
        for r in rows:
            r[2] = mapping.get(r[2], r[2])
            r[4] = mapping.get(r[4], r[4])

    rows.sort(key=lambda r: (r[0], r[1], r[6] == "scheduled"))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["season", "week", "manager_a", "score_a", "manager_b", "score_b", "type", "proj_a", "proj_b"])
        w.writerows([(r + ["", ""])[:9] for r in rows])
    played = [r for r in rows if r[6] not in ("scheduled", "strength")]
    upcoming = [r for r in rows if r[6] == "scheduled"]
    seasons = sorted({r[0] for r in played})
    finals = sum(1 for r in played if r[6] == "final")
    print(f"Wrote {len(played)} games across {len(seasons)} seasons ({finals} title games) to {args.out}")
    if upcoming:
        wks = sorted({r[1] for r in upcoming})
        print(f"Plus {len(upcoming)} upcoming matchups for {upcoming[0][0]}, weeks {wks[0]}-{wks[-1]} (used for the preview, picks and sportsbook)")
    if finals < len(seasons) - (1 if upcoming else 0):
        print("A finished season has no title game. Mark the championship row's type as 'final' by hand if needed.")
    managers = sorted({r[2] for r in played} | {r[4] for r in played})
    print("Managers found:", ", ".join(managers))
    print("If one person shows up under two names, fix it with --rename.")


if __name__ == "__main__":
    main()
