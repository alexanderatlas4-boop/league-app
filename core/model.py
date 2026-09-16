"""League math: stats, projections, lines, season simulation, bet grading.

Everything works on plain dicts so it is easy to test. Games look like:
    {"season", "week", "a", "sa", "b", "sb", "type", "pa", "pb"}
type is regular | playoff | final | consolation | scheduled.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean, stdev
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ESPN_WEIGHT = 0.7          # weekly projection = 70% ESPN starters, 30% scoring history
ROSTER_WEIGHT = 0.65       # season strength = 65% ESPN best-lineup projection, 35% history
SEASON_KEEP = 0.75         # tone down team differences over a season
PRIOR_KEEP = 0.4           # how much of last season's edge carries into this season
PRIOR_GAMES = 5            # weight of that prior, in games
SIMS = 4000


def is_reg(g): return g["type"] == "regular"
def is_post(g): return g["type"] in ("playoff", "final")
def counted(g): return g["type"] not in ("consolation", "scheduled")


def winner(g):
    if g["sa"] > g["sb"]: return g["a"]
    if g["sb"] > g["sa"]: return g["b"]
    return None


def sides(g):
    return [dict(m=g["a"], o=g["b"], pf=g["sa"], pa=g["sb"], g=g),
            dict(m=g["b"], o=g["a"], pf=g["sb"], pa=g["sa"], g=g)]


def gkey(g):
    a, b = sorted([g["a"], g["b"]])
    return f"{g['season']}|{g['week']}|{a}|{b}"


def phi(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))
def half(x): return round(x * 2) / 2


def american(p, hold=0.045):
    imp = min(0.985, max(0.0099, p * (1 + hold)))
    dec = 1 / imp
    a = (dec - 1) * 100 if dec >= 2 else -100 / (dec - 1)
    a = int(round(a / 5) * 5)
    if -100 < a < 100:
        a = 100 if a >= 0 else -100
    return a


def fmt_odds(a): return f"+{a}" if a > 0 else str(a)
def fmt_spread(x): return "PK" if x == 0 else f"{x:+.1f}"
def profit(stake, price): return stake * price / 100 if price > 0 else stake * 100 / -price
def record(w, l, t=0): return f"{w}–{l}" + (f"–{t}" if t else "")


class League:
    def __init__(self, games: list[dict], strength: dict | None = None, renames: dict | None = None):
        ren = renames or {}
        nm = lambda x: ren.get(x, x)
        allg = []
        for g in games:
            g = dict(g)
            g["a"], g["b"] = nm(g["a"]), nm(g["b"])
            allg.append(g)
        allg.sort(key=lambda g: (g["season"], g["week"]))
        self.games = [g for g in allg if g["type"] != "scheduled"]
        last = self.games[-1] if self.games else None
        self.upcoming = [g for g in allg if g["type"] == "scheduled"
                         and (not last or (g["season"], g["week"]) > (last["season"], last["week"]))]
        self.strength = {int(s): {nm(k): v for k, v in d.items()} for s, d in (strength or {}).items()}
        self.seasons = sorted({g["season"] for g in self.games})
        self.managers = sorted({x for g in self.games for x in (g["a"], g["b"])}, key=str.lower)
        self.champs, self.runners = {}, {}
        for g in self.games:
            if g["type"] == "final" and winner(g):
                w = winner(g)
                self.champs[g["season"]] = w
                self.runners[g["season"]] = g["b"] if w == g["a"] else g["a"]
        self.post_seasons = {(x, g["season"]) for g in self.games if is_post(g) for x in (g["a"], g["b"])}
        self._sd = None
        self._build_tables()

    # ------------------------------------------------------------ tables
    def _build_tables(self):
        by_week = defaultdict(list)
        for g in self.games:
            if is_reg(g):
                by_week[(g["season"], g["week"])] += [(g["a"], g["sa"]), (g["b"], g["sb"])]
        self.all_play = defaultdict(lambda: dict(w=0, l=0, t=0, xw=0.0))
        self.all_play_season = defaultdict(lambda: defaultdict(lambda: dict(w=0, l=0, t=0, xw=0.0)))
        for (s, _), lst in by_week.items():
            n = len(lst) - 1
            for m, sc in lst:
                beat = sum(1 for m2, s2 in lst if m2 != m and sc > s2)
                lost = sum(1 for m2, s2 in lst if m2 != m and sc < s2)
                tied = n - beat - lost
                for r in (self.all_play[m], self.all_play_season[s][m]):
                    r["w"] += beat; r["l"] += lost; r["t"] += tied
                    r["xw"] += (beat + tied / 2) / n if n else 0
        self.season_tables = {}
        for s in self.seasons:
            t = {}
            for g in self.games:
                if g["season"] == s and is_reg(g):
                    for x in sides(g):
                        r = t.setdefault(x["m"], dict(m=x["m"], w=0, l=0, t=0, pf=0.0, pa=0.0, gp=0))
                        r["gp"] += 1; r["pf"] += x["pf"]; r["pa"] += x["pa"]
                        if x["pf"] > x["pa"]: r["w"] += 1
                        elif x["pf"] < x["pa"]: r["l"] += 1
                        else: r["t"] += 1
            rows = list(t.values())
            for r in rows:
                r["winpct"] = (r["w"] + r["t"] / 2) / r["gp"]
                ap = self.all_play_season[s].get(r["m"])
                r["ap"] = (ap["w"] + ap["t"] / 2) / max(1, ap["w"] + ap["l"] + ap["t"]) if ap else 0
                r["xw"] = ap["xw"] if ap else 0
                r["luck"] = r["w"] + r["t"] / 2 - r["xw"]
            rows.sort(key=lambda r: (-r["winpct"], -r["pf"]))
            for i, r in enumerate(rows):
                r["rank"] = i + 1
            self.season_tables[s] = rows

    def table_row(self, s, m):
        return next((r for r in self.season_tables.get(s, []) if r["m"] == m), None)

    # ------------------------------------------------------------ all-time stats
    def scope(self, include_playoffs):
        return (lambda g: is_reg(g) or is_post(g)) if include_playoffs else is_reg

    def manager_stats(self, include_playoffs=False):
        f = self.scope(include_playoffs)
        R = {m: dict(m=m, w=0, l=0, t=0, pf=0.0, pa=0.0, gp=0, hi=None, lo=None, seas=set(), pw=0, pl=0,
                     titles=0, finals=0) for m in self.managers}
        for g in self.games:
            for x in sides(g):
                r = R[x["m"]]
                if g["type"] != "consolation": r["seas"].add(g["season"])
                if is_post(g):
                    if x["pf"] > x["pa"]: r["pw"] += 1
                    elif x["pf"] < x["pa"]: r["pl"] += 1
                if not f(g): continue
                r["gp"] += 1; r["pf"] += x["pf"]; r["pa"] += x["pa"]
                if x["pf"] > x["pa"]: r["w"] += 1
                elif x["pf"] < x["pa"]: r["l"] += 1
                else: r["t"] += 1
                r["hi"] = x["pf"] if r["hi"] is None else max(r["hi"], x["pf"])
                r["lo"] = x["pf"] if r["lo"] is None else min(r["lo"], x["pf"])
        for s, c in self.champs.items():
            R[c]["titles"] += 1; R[c]["finals"] += 1; R[self.runners[s]]["finals"] += 1
        out = []
        for m, r in R.items():
            if not r["gp"]: continue
            ap = self.all_play.get(m)
            regw = sum(x["w"] + x["t"] / 2 for s in self.seasons for x in self.season_tables[s] if x["m"] == m)
            r.update(seasons=len(r["seas"]), playoff_years=sum(1 for s in self.seasons if (m, s) in self.post_seasons),
                     winpct=(r["w"] + r["t"] / 2) / r["gp"], ppg=r["pf"] / r["gp"], papg=r["pa"] / r["gp"],
                     ap=(ap["w"] + ap["t"] / 2) / max(1, ap["w"] + ap["l"] + ap["t"]) if ap else 0,
                     luck=regw - ap["xw"] if ap else 0)
            r["margin"] = r["ppg"] - r["papg"]
            out.append(r)
        return out

    def h2h(self, include_playoffs=False):
        f = self.scope(include_playoffs)
        M = defaultdict(lambda: defaultdict(lambda: dict(w=0, l=0, t=0, pf=0.0, pa=0.0, games=[])))
        for g in self.games:
            if not f(g): continue
            for x in sides(g):
                r = M[x["m"]][x["o"]]
                if x["pf"] > x["pa"]: r["w"] += 1
                elif x["pf"] < x["pa"]: r["l"] += 1
                else: r["t"] += 1
                r["pf"] += x["pf"]; r["pa"] += x["pa"]; r["games"].append(x)
        return M

    def streaks(self, include_playoffs=False, through=None):
        f = self.scope(include_playoffs) if through is None else counted
        per = defaultdict(list)
        for g in self.games:
            if not f(g): continue
            if through and (g["season"], g["week"]) > through: continue
            for x in sides(g):
                per[x["m"]].append(x)
        out = {}
        for m, lst in per.items():
            bw = dict(n=0); bl = dict(n=0); cur = dict(k=None, n=0, start=None)
            for x in lst:
                k = "W" if x["pf"] > x["pa"] else "L" if x["pf"] < x["pa"] else "T"
                cur = dict(k=k, n=cur["n"] + 1, start=cur["start"]) if k == cur["k"] else dict(k=k, n=1, start=x["g"])
                if k == "W" and cur["n"] > bw["n"]: bw = dict(n=cur["n"], start=cur["start"], end=x["g"])
                if k == "L" and cur["n"] > bl["n"]: bl = dict(n=cur["n"], start=cur["start"], end=x["g"])
            out[m] = dict(best_win=bw, best_loss=bl, current=cur)
        return out

    def series(self, a, b, through=None):
        r = dict(w=0, l=0, t=0, games=[])
        for g in self.games:
            if not counted(g) or {g["a"], g["b"]} != {a, b}: continue
            if through and (g["season"], g["week"]) > through: continue
            x = sides(g)[0] if g["a"] == a else sides(g)[1]
            if x["pf"] > x["pa"]: r["w"] += 1
            elif x["pf"] < x["pa"]: r["l"] += 1
            else: r["t"] += 1
            r["games"].append(x)
        k, n = None, 0
        for x in reversed(r["games"]):
            who = a if x["pf"] > x["pa"] else b if x["pf"] < x["pa"] else None
            if k is None: k = who
            if who != k or who is None: break
            n += 1
        r["streak"] = (k, n)
        return r

    @staticmethod
    def series_text(a, b, r):
        if not r["games"]: return "First meeting"
        if r["w"] == r["l"]: return f"Series tied {record(r['w'], r['l'], r['t'])}"
        return (f"{a} leads the series {record(r['w'], r['l'], r['t'])}" if r["w"] > r["l"]
                else f"{b} leads the series {record(r['l'], r['w'], r['t'])}")

    # ------------------------------------------------------------ projections
    def score_sd(self):
        if self._sd is None:
            recent = set(self.seasons[-3:])
            xs = [v for g in self.games if is_reg(g) and g["season"] in recent for v in (g["sa"], g["sb"])]
            self._sd = stdev(xs) if len(xs) >= 20 else 25.0
        return self._sd

    def project(self, m, s, w):
        cur = [x["pf"] for g in self.games if g["season"] == s and g["week"] < w and (is_reg(g) or is_post(g))
               for x in sides(g) if x["m"] == m]
        prev = self.season_tables.get(s - 1, [])
        if prev:
            lg = sum(r["pf"] for r in prev) / sum(r["gp"] for r in prev)
        else:
            allv = [v for g in self.games if is_reg(g) for v in (g["sa"], g["sb"])]
            lg = mean(allv) if allv else 110.0
        prow = next((r for r in prev if r["m"] == m), None)
        prior = lg + PRIOR_KEEP * (prow["pf"] / prow["gp"] - lg) if prow else lg
        sd = self.score_sd()
        damp = [min(lg + 2 * sd, max(lg - 2 * sd, v)) for v in cur]
        n = len(cur)
        est = (sum(damp) + PRIOR_GAMES * prior) / (n + PRIOR_GAMES)
        last3 = damp[-3:]
        proj = 0.7 * est + 0.3 * mean(last3) if len(last3) == 3 else est
        return dict(proj=proj, hist=proj, n=n, avg=mean(cur) if n else None, espn=None)

    def forecast(self, g):
        A = self.project(g["a"], g["season"], g["week"])
        B = self.project(g["b"], g["season"], g["week"])
        for X, e in ((A, g.get("pa")), (B, g.get("pb"))):
            if e and e > 0:
                X["espn"] = e
                X["proj"] = ESPN_WEIGHT * e + (1 - ESPN_WEIGHT) * X["hist"]
        p = phi((A["proj"] - B["proj"]) / (self.score_sd() * math.sqrt(2)))
        return dict(A=A, B=B, pA=p)

    def standings_through(self, s, w):
        t = {}
        for g in self.games:
            if g["season"] == s and is_reg(g) and g["week"] <= w:
                for x in sides(g):
                    r = t.setdefault(x["m"], dict(m=x["m"], w=0, l=0, t=0, pf=0.0, gp=0))
                    r["gp"] += 1; r["pf"] += x["pf"]
                    if x["pf"] > x["pa"]: r["w"] += 1
                    elif x["pf"] < x["pa"]: r["l"] += 1
                    else: r["t"] += 1
        rows = sorted(t.values(), key=lambda r: (-(r["w"] + r["t"] / 2) / r["gp"], -r["pf"]))
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        return rows

    def next_week(self):
        if not self.upcoming: return None
        s, w = self.upcoming[0]["season"], self.upcoming[0]["week"]
        return s, w, [g for g in self.upcoming if g["season"] == s and g["week"] == w]

    def current_season(self):
        return self.upcoming[0]["season"] if self.upcoming else (self.seasons[-1] if self.seasons else date.today().year)

    # ------------------------------------------------------------ recap & preview
    def recap(self):
        pl = [g for g in self.games if counted(g)]
        if not pl: return None
        s, w = pl[-1]["season"], pl[-1]["week"]
        games = [g for g in pl if g["season"] == s and g["week"] == w]
        ws = [x for g in games for x in sides(g)]
        all_sides = [x for g in pl for x in sides(g)]
        st = self.streaks(through=(s, w))
        rows = []
        for g in games:
            W, Lo = sorted(sides(g), key=lambda x: -x["pf"])
            notes = []
            if g["type"] == "final": notes.append(f"{W['m']} wins the {s} title")
            for x in (W, Lo):
                rank = sum(1 for y in all_sides if y["pf"] > x["pf"]) + 1
                mine = [y["pf"] for y in all_sides if y["m"] == x["m"]]
                if rank <= 5: notes.append(f"{x['m']}'s {x['pf']:.2f} is the #{rank} score in league history")
                elif len(mine) > 8 and x["pf"] >= max(mine): notes.append(f"Career high for {x['m']}")
                if len(mine) > 8 and x["pf"] <= min(mine): notes.append(f"Career low for {x['m']}")
                cur = st.get(x["m"], {}).get("current", {})
                if cur.get("n", 0) >= 3:
                    notes.append(f"{x['m']} has {cur['n']} straight {'wins' if cur['k'] == 'W' else 'losses' if cur['k'] == 'L' else 'ties'}")
            notes.append(self.series_text(W["m"], Lo["m"], self.series(W["m"], Lo["m"], through=(s, w))))
            rows.append(dict(winner=W["m"], w_score=W["pf"], loser=Lo["m"], l_score=Lo["pf"],
                             margin=W["pf"] - Lo["pf"], notes=notes, type=g["type"]))
        rows.sort(key=lambda r: -r["w_score"])
        n = len(ws)
        for x in ws:
            x["beat"] = sum(1 for y in ws if y is not x and y["pf"] < x["pf"])
        losers = sorted([x for x in ws if x["pf"] < x["pa"]], key=lambda x: -x["pf"])
        winners_ = sorted([x for x in ws if x["pf"] > x["pa"]], key=lambda x: x["pf"])
        return dict(season=s, week=w, rows=rows, n=n, regular=all(is_reg(g) for g in games),
                    avg=mean(x["pf"] for x in ws),
                    top=max(ws, key=lambda x: x["pf"]), bottom=min(ws, key=lambda x: x["pf"]),
                    unlucky=losers[0] if losers else None, lucky=winners_[0] if winners_ else None)

    def preview(self):
        nw = self.next_week()
        if not nw: return None
        s, w, games = nw
        table = {r["m"]: r for r in self.standings_through(s, w - 1)}
        cards = []
        for g in games:
            F = self.forecast(g)
            ser = self.series(g["a"], g["b"])
            story = [self.series_text(g["a"], g["b"], ser) + ("." if ser["games"] else " in league records.")]
            who, k = ser["streak"]
            if k >= 3: story.append(f"{who} has won the last {k} meetings.")
            if ser["games"]:
                lm = ser["games"][-1]
                story.append(f"Last meeting: {g['a'] if lm['pf'] > lm['pa'] else g['b']} won "
                             f"{max(lm['pf'], lm['pa']):.2f} to {min(lm['pf'], lm['pa']):.2f} ({lm['g']['season']}, week {lm['g']['week']}).")
                for x in ser["games"]:
                    if is_post(x["g"]):
                        story.append(f"Rematch of the {x['g']['season']} {'title game' if x['g']['type'] == 'final' else 'playoffs'}, "
                                     f"won by {g['a'] if x['pf'] > x['pa'] else g['b']}.")
            cards.append(dict(game=g, key=gkey(g), A=F["A"], B=F["B"], pA=F["pA"], story=story,
                              rec_a=table.get(g["a"]), rec_b=table.get(g["b"])))
        if cards:
            gotw = max(cards, key=lambda c: c["A"]["proj"] + c["B"]["proj"] - 3 * abs(c["A"]["proj"] - c["B"]["proj"]))
            gotw["gotw"] = True
            cards.sort(key=lambda c: (not c.get("gotw"), abs(c["pA"] - 0.5)))
        return dict(season=s, week=w, cards=cards, sd=self.score_sd())

    # ------------------------------------------------------------ markets
    def week_markets(self):
        P = self.preview()
        if not P: return None
        ms = []
        for c in P["cards"]:
            a, b, k = c["game"]["a"], c["game"]["b"], c["key"]
            sp = half(c["A"]["proj"] - c["B"]["proj"])
            tot = half(c["A"]["proj"] + c["B"]["proj"])
            t = f"{a} vs {b}"
            ms.append(dict(id=f"ml|{k}", kind="ml", title=t, name="Moneyline", sides=[
                dict(id="a", team=a, label=a, line=None, price=american(c["pA"])),
                dict(id="b", team=b, label=b, line=None, price=american(1 - c["pA"]))]))
            ms.append(dict(id=f"sp|{k}", kind="sp", title=t, name="Spread", sides=[
                dict(id="a", team=a, label=a, line=-sp, price=-110),
                dict(id="b", team=b, label=b, line=sp, price=-110)]))
            ms.append(dict(id=f"tot|{k}", kind="tot", title=t, name="Total points", sides=[
                dict(id="o", team=None, label="Over", line=tot, price=-110),
                dict(id="u", team=None, label="Under", line=tot, price=-110)]))
        return dict(mk=f"{P['season']}|{P['week']}", season=P["season"], week=P["week"], markets=ms)

    def reg_weeks(self):
        for s in reversed(self.seasons):
            if s in self.champs:
                return max(g["week"] for g in self.games if g["season"] == s and is_reg(g))
        return 14

    def playoff_teams(self):
        for s in reversed(self.seasons):
            if s in self.champs:
                n = len({x for g in self.games if g["season"] == s and is_post(g) for x in (g["a"], g["b"])})
                if n: return n
        return 6

    def season_info(self):
        s, reg = self.current_season(), self.reg_weeks()
        played = [g for g in self.games if g["season"] == s and is_reg(g)]
        sched = [g for g in self.upcoming if g["season"] == s and g["week"] <= reg]
        max_w = max([0] + [g["week"] for g in played + sched])
        return dict(season=s, reg=reg, played=played, sched=sched, ready=max_w >= reg,
                    done=s in self.champs, started=any(g["season"] == s and is_post(g) for g in self.games))

    def season_strength(self, s, names, next_w):
        st = self.strength.get(s, {})
        hist = {m: self.project(m, s, next_w)["proj"] for m in names}
        hm = mean(hist.values())
        have = [m for m in names if st.get(m, 0) > 0]
        use_roster = len(have) >= max(2, len(names) - 1)
        sm = mean(st[m] for m in have) if have else 0
        out = {}
        for m in names:
            hr = hist[m] - hm
            rr = st[m] - sm if use_roster and st.get(m, 0) > 0 else None
            rel = hr if rr is None else ROSTER_WEIGHT * rr + (1 - ROSTER_WEIGHT) * hr
            out[m] = dict(mu=hm + SEASON_KEEP * rel, hist=hist[m], roster=st.get(m))
        return out, use_roster

    def simulate(self, playoff_teams=None, n=SIMS):
        info = self.season_info()
        if not info["ready"] or info["done"] or info["started"]:
            return None
        s, sd = info["season"], self.score_sd()
        T = defaultdict(lambda: dict(w=0.0, pf=0.0))
        for g in info["played"]:
            for x in sides(g):
                T[x["m"]]["pf"] += x["pf"]
                T[x["m"]]["w"] += 1 if x["pf"] > x["pa"] else 0.5 if x["pf"] == x["pa"] else 0
        for g in info["sched"]:
            T[g["a"]]; T[g["b"]]
        names = sorted(T, key=str.lower)
        next_w = min((g["week"] for g in info["sched"]), default=info["reg"] + 1)
        SS, used_roster = self.season_strength(s, names, next_w)
        mu = {m: SS[m]["mu"] for m in names}
        shift = {}
        for i, g in enumerate(info["sched"]):
            if g["week"] == next_w and (g.get("pa") or g.get("pb")):
                F = self.forecast(g)
                shift[i] = (F["A"]["proj"] - mu[g["a"]], F["B"]["proj"] - mu[g["b"]])
        P = max(2, min(playoff_teams or self.playoff_teams(), len(names)))
        size = 1
        while size < P: size *= 2
        byes = size - P
        gp = len(info["played"]) * 2 / max(1, len(names))
        tau = math.sqrt(49 * 6 / (gp + 6) + 25)
        seed = len(info["played"]) * 7919 + round(sum(g["sa"] + g["sb"] for g in info["played"]))
        rng = random.Random(seed)
        gauss = rng.gauss
        tally = {m: dict(title=0, pf_top=0, wins=[], po=0, bye=0, final=0) for m in names}
        for _ in range(n):
            MU = {m: mu[m] + tau * gauss(0, 1) for m in names}
            w = {m: T[m]["w"] for m in names}
            pf = {m: T[m]["pf"] for m in names}
            for i, g in enumerate(info["sched"]):
                sh = shift.get(i, (0, 0))
                sa = MU[g["a"]] + sh[0] + sd * gauss(0, 1)
                sb = MU[g["b"]] + sh[1] + sd * gauss(0, 1)
                pf[g["a"]] += sa; pf[g["b"]] += sb
                if sa >= sb: w[g["a"]] += 1
                else: w[g["b"]] += 1
            for m in names:
                tally[m]["wins"].append(w[m])
            tally[max(names, key=lambda m: pf[m])]["pf_top"] += 1
            seeds = sorted(names, key=lambda m: (-w[m], -pf[m]))[:P]
            rank = {m: i for i, m in enumerate(seeds)}
            for i, m in enumerate(seeds):
                tally[m]["po"] += 1
                if i < byes: tally[m]["bye"] += 1
            play = lambda a, b: a if MU[a] + sd * gauss(0, 1) >= MU[b] + sd * gauss(0, 1) else b
            adv, rest = seeds[:byes], seeds[byes:]
            while rest:
                adv.append(play(rest.pop(0), rest.pop()))
            alive = sorted(adv, key=rank.get)
            while len(alive) > 1:
                if len(alive) == 2:
                    tally[alive[0]]["final"] += 1; tally[alive[1]]["final"] += 1
                r, nxt = list(alive), []
                while r:
                    nxt.append(play(r.pop(0), r.pop()))
                alive = sorted(nxt, key=rank.get)
            tally[alive[0]]["title"] += 1
        race = []
        for m in names:
            t, row = tally[m], self.table_row(s, m)
            race.append(dict(m=m, record=record(row["w"], row["l"], row["t"]) if row else "0–0",
                             roster=SS[m]["roster"], hist=SS[m]["hist"], wins=mean(t["wins"]),
                             po=t["po"] / n, bye=t["bye"] / n, final=t["final"] / n, title=t["title"] / n,
                             pf_top=t["pf_top"] / n, wins_list=t["wins"]))
        race.sort(key=lambda r: -r["title"])
        return dict(season=s, race=race, P=P, byes=byes, used_roster=used_roster, n=n)

    def season_markets(self, playoff_teams=None):
        sim = self.simulate(playoff_teams)
        if not sim: return None, None
        s = sim["season"]
        ms = []
        for kind, key, name in (("title", "title", "League champion"), ("pf", "pf_top", "Most regular-season points")):
            opts = sorted(sim["race"], key=lambda r: -r[key])
            ms.append(dict(id=f"{kind}|{s}", kind=kind, title=name, name=name,
                           sides=[dict(id=r["m"], team=r["m"], label=r["m"], line=None, price=american(r[key], 0.25)) for r in opts]))
        for r in sorted(sim["race"], key=lambda r: r["m"].lower()):
            line = math.floor(r["wins"]) + 0.5
            p_over = sum(1 for x in r["wins_list"] if x > line) / sim["n"]
            ms.append(dict(id=f"wins|{s}|{r['m']}", kind="wins", title=r["m"], name="Regular-season wins", sides=[
                dict(id="o", team=r["m"], label="Over", line=line, price=american(p_over)),
                dict(id="u", team=r["m"], label="Under", line=line, price=american(1 - p_over))]))
            ms.append(dict(id=f"po|{s}|{r['m']}", kind="po", title=r["m"], name="Make the playoffs", sides=[
                dict(id="y", team=r["m"], label="Yes", line=None, price=american(r["po"])),
                dict(id="n", team=r["m"], label="No", line=None, price=american(1 - r["po"]))]))
        return dict(mk=f"{s}|season", season=s, markets=ms), sim

    # ------------------------------------------------------------ bets
    def grade(self, bet):
        if bet.get("grade"): return bet["grade"]
        kind = bet["market_id"].split("|")[0]
        if kind in ("ml", "sp", "tot"):
            key = bet["market_id"][len(kind) + 1:]
            g = next((x for x in self.games if counted(x) and gkey(x) == key), None)
            if not g: return None
            if kind == "tot":
                tot = g["sa"] + g["sb"]
                if tot == bet["line"]: return "push"
                return "win" if (bet["side"] == "o") == (tot > bet["line"]) else "loss"
            if bet["team"] not in (g["a"], g["b"]): return "void"
            ts, os_ = (g["sa"], g["sb"]) if bet["team"] == g["a"] else (g["sb"], g["sa"])
            m = ts - os_ + (bet["line"] if kind == "sp" else 0)
            return "win" if m > 0 else "loss" if m < 0 else "push"
        s = int(bet["market_id"].split("|")[1])
        if s not in self.champs: return None
        if kind == "title": return "win" if self.champs[s] == bet["team"] else "loss"
        if kind == "pf":
            top = max(self.season_tables[s], key=lambda r: r["pf"])
            return "win" if top["m"] == bet["team"] else "loss"
        if kind == "po":
            made = (bet["team"], s) in self.post_seasons
            return "win" if (bet["side"] == "y") == made else "loss"
        if kind == "wins":
            r = self.table_row(s, bet["team"])
            if not r: return "void"
            wv = r["w"] + r["t"] / 2
            if wv == bet["line"]: return "push"
            return "win" if (bet["side"] == "o") == (wv > bet["line"]) else "loss"
        return None

    def weeks_played(self, s):
        return len({g["week"] for g in self.games if g["season"] == s and counted(g)})

    def ledger(self, s, bets, bankroll, top_up):
        bonus = top_up * self.weeks_played(s)
        rows = {m: dict(m=m, start=bankroll + bonus, bonus=bonus, pl=0.0, risk=0, pending=0, w=0, l=0, p=0, bets=0)
                for m in self.managers}
        for b in bets:
            if int(b["market_key"].split("|")[0]) != s or b["team_name"] not in rows: continue
            r = rows[b["team_name"]]
            if b["status"] == "pending": r["pending"] += b["stake"]; continue
            if b["status"] != "open": continue
            gr = self.grade(b)
            r["bets"] += 1
            if not gr: r["risk"] += b["stake"]
            elif gr == "win": r["pl"] += profit(b["stake"], b["price"]); r["w"] += 1
            elif gr == "loss": r["pl"] -= b["stake"]; r["l"] += 1
            elif gr == "push": r["p"] += 1
        for r in rows.values():
            r["balance"] = r["start"] + r["pl"]
            r["available"] = r["balance"] - r["risk"] - r["pending"]
        return rows

    def pick_standings(self, s, picks):
        by_week = defaultdict(lambda: defaultdict(dict))
        for p in picks:
            if p["season"] == s:
                by_week[p["week"]][p["team_name"]][p["game_key"]] = p["pick"]
        rows = {}
        for w in sorted(by_week):
            results = {gkey(g): winner(g) for g in self.games if g["season"] == s and g["week"] == w and counted(g)}
            if not results: continue
            scores = {}
            for m, ps in by_week[w].items():
                graded = [(k, v) for k, v in ps.items() if results.get(k)]
                c = sum(1 for k, v in graded if results[k] == v)
                r = rows.setdefault(m, dict(m=m, correct=0, total=0, weeks_won=0, latest=""))
                r["correct"] += c; r["total"] += len(graded); r["latest"] = f"{c} of {len(graded)} (week {w})"
                scores[m] = c
            top = max(scores.values()) if scores else 0
            if len(scores) > 1 and top > 0:
                for m, c in scores.items():
                    if c == top: rows[m]["weeks_won"] += 1
        return sorted(rows.values(), key=lambda r: (-r["correct"], r["total"] - r["correct"], -r["weeks_won"]))


def market_label(bet):
    kind, side, team, line = bet["market_id"].split("|")[0], bet["side"], bet.get("team"), bet.get("line")
    if kind == "ml": return f"{team} to win"
    if kind == "sp": return f"{team} {fmt_spread(line)}"
    if kind == "tot": return f"{'Over' if side == 'o' else 'Under'} {line:.1f} total points"
    if kind == "title": return f"{team} to win the title"
    if kind == "pf": return f"{team} to lead the league in points"
    if kind == "wins": return f"{team} {'over' if side == 'o' else 'under'} {line:.1f} wins"
    if kind == "po": return f"{team} to {'make' if side == 'y' else 'miss'} the playoffs"
    return bet["market_id"]


def against_self(me, market_id, side, team, allow=False):
    if allow or not me: return ""
    kind = market_id.split("|")[0]
    if kind in ("ml", "sp", "tot"):
        if me not in market_id.split("|")[3:]: return ""
        if kind == "tot": return "You can't bet the under in your own game." if side == "u" else ""
        return "You can't bet against your own team." if team != me else ""
    if team == me and ((kind == "wins" and side == "u") or (kind == "po" and side == "n")):
        return "You can't bet against your own team."
    return ""


def lock_time(kickoff: str | None, lock_at: str, week: int):
    """Thursday-night lock for a week, from the season's week-1 Thursday date (ET)."""
    if not kickoff: return None
    try:
        d = date.fromisoformat(kickoff) + timedelta(days=7 * (week - 1))
        hh, mm = (int(x) for x in (lock_at or "20:15").split(":"))
    except ValueError:
        return None
    return datetime.combine(d, time(hh, mm), tzinfo=ET)


def now_utc():
    return datetime.now(timezone.utc)
