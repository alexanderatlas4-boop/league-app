"""The weekly column, written by Claude from the league's own numbers.

facts() turns a League into a compact dict of everything that happened this week.
write() sends those facts to Claude and returns the column text. No key, no call:
the page falls back to the column it writes itself.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from statistics import mean

from .model import counted, record, sides

MODEL = "claude-sonnet-5-5"
API = "https://api.anthropic.com/v1/messages"

VOICE = """You write the weekly column for a 10-team fantasy football league of close friends.

Your job is to be funny. Think group chat at its best: quick, mean in the way friends are
mean, never actually cruel. Roast the bad lineups, celebrate the lucky idiots, treat a
78-point loss as the tragedy it is. Jokes come from the numbers in front of you, not from
stock fantasy-football patter.

Rules:
- Use ONLY the facts given. Never invent players, trades, injuries or quotes. If you want a
  joke about someone's roster, make it about their scores, streaks or record.
- Keep the needling about fantasy performance. Nothing about anyone's looks, family, job,
  intelligence or anything outside this league.
- 250 to 350 words total.
- Structure: a punchy opening paragraph on the week, then a POWER RANKINGS list (1 to 10,
  each line "N. Name (record, points per week) — one funny sentence"), then a short
  LOOKING AHEAD paragraph ending with one bold prediction.
- Plain text. No markdown, no emoji, no headers other than POWER RANKINGS and LOOKING AHEAD.
- Vary your openings week to week. Never start with "Well," or "Folks,"."""


def power(L, s, w):
    """Rank the league: season scoring, recent form, all-play and actual record."""
    per, weeks = {}, {}
    for g in L.games:
        if not counted(g) or g["season"] != s or g["week"] > w:
            continue
        for x in sides(g):
            per.setdefault(x["m"], []).append(dict(week=g["week"], pf=x["pf"], pa=x["pa"]))
            weeks.setdefault(g["week"], []).append((x["m"], x["pf"]))
    if not per:
        return []
    allpf = [x["pf"] for v in per.values() for x in v]
    avg, sd = mean(allpf), L.score_sd()
    rows = []
    for m, games in per.items():
        games.sort(key=lambda x: x["week"])
        pfs = [x["pf"] for x in games]
        beat = tot = 0.0
        for x in games:
            for om, opf in weeks.get(x["week"], []):
                if om == m:
                    continue
                tot += 1
                beat += 1 if x["pf"] > opf else 0.5 if x["pf"] == opf else 0
        ap = beat / tot if tot else 0.5
        wins = sum(1 for x in games if x["pf"] > x["pa"])
        ties = sum(1 for x in games if x["pf"] == x["pa"])
        winpct = (wins + ties / 2) / len(games)
        streak_k, streak_n = None, 0
        for x in reversed(games):
            r = "W" if x["pf"] > x["pa"] else "L" if x["pf"] < x["pa"] else "T"
            if streak_k is None:
                streak_k = r
            if r != streak_k:
                break
            streak_n += 1
        rows.append(dict(
            manager=m, record=record(wins, len(games) - wins - ties, ties), ppg=round(mean(pfs), 1),
            last3=round(mean(pfs[-3:]), 1), all_play=round(ap, 3), streak=f"{streak_k}{streak_n}",
            score=0.40 * mean(pfs) + 0.25 * mean(pfs[-3:]) + 0.20 * (avg + (ap - 0.5) * 2 * sd)
                  + 0.15 * (avg + (winpct - 0.5) * 2 * sd)))
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r.pop("score")
    return rows


def facts(L):
    """Everything the column can talk about, as plain data."""
    R, P = L.recap(), L.preview()
    out = {"league": "fantasy football", "teams": len(L.managers)}
    if R:
        out["week"] = {"season": R["season"], "number": R["week"], "league_average": round(R["avg"], 1)}
        out["results"] = [dict(winner=r["winner"], winner_score=round(r["w_score"], 2),
                               loser=r["loser"], loser_score=round(r["l_score"], 2),
                               margin=round(r["margin"], 2), notes=r["notes"]) for r in R["rows"]]
        out["high_score"] = dict(manager=R["top"]["m"], points=round(R["top"]["pf"], 2))
        out["low_score"] = dict(manager=R["bottom"]["m"], points=round(R["bottom"]["pf"], 2))
        if R["unlucky"]:
            out["unluckiest_loss"] = dict(manager=R["unlucky"]["m"], points=round(R["unlucky"]["pf"], 2),
                                          lost_to=R["unlucky"]["o"], outscored_teams=R["unlucky"].get("beat"))
        if R["lucky"]:
            out["luckiest_win"] = dict(manager=R["lucky"]["m"], points=round(R["lucky"]["pf"], 2),
                                       beat=R["lucky"]["o"], outscored_teams=R["lucky"].get("beat"))
        out["power_rankings"] = power(L, R["season"], R["week"])
        out["standings"] = [dict(rank=r["rank"], manager=r["m"], record=record(r["w"], r["l"], r["t"]),
                                 points_for=round(r["pf"], 1)) for r in L.standings_through(R["season"], R["week"])]
    if P:
        out["next_week"] = {"number": P["week"], "matchups": [
            dict(a=c["game"]["a"], b=c["game"]["b"], a_win_chance=round(c["pA"] * 100),
                 game_of_the_week=bool(c.get("gotw")), history=c["story"][:2]) for c in P["cards"]]}
    return out


def write(L, api_key, model=None, timeout=60):
    """Ask Claude for this week's column. Returns text, or raises with a readable message."""
    body = json.dumps({
        "model": model or MODEL,
        "max_tokens": 1200,
        "system": VOICE,
        "messages": [{"role": "user", "content":
                      "Here is this week in the league, as JSON. Write the column.\n\n"
                      + json.dumps(facts(L), default=str)}],
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            pass
        raise RuntimeError(f"Claude returned {e.code}. {detail}".strip())
    except Exception as e:
        raise RuntimeError(f"Could not reach Claude: {e}")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("Claude returned an empty column.")
    return text
