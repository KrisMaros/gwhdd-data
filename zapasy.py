"""Tygodniowy raport EIA o zapasach gazu (Weekly Natural Gas Storage Report) przez API EIA v2 – GitHub Actions.
Klucz API w sekrecie repozytorium EIA_API_KEY.
Wyniki: data/storage_weekly.csv, data/storage_regions.csv, data/storage_status.json.
Średnia 5-letnia i stan sprzed roku liczone z historii EIA (ten sam tydzień w poprzednich latach, ±3 dni)."""
import csv, json, os
from datetime import date, datetime, timedelta, timezone
import requests

D = "data"
os.makedirs(D, exist_ok=True)
KEY = os.environ.get("EIA_API_KEY", "").strip()
URL = "https://api.eia.gov/v2/natural-gas/stor/wkly/data/"
SERIES = [  # (seria EIA, nazwa w panelu)
    ("NW2_EPG0_SWO_R48_BCF", "Razem Lower 48"),
    ("NW2_EPG0_SWO_R31_BCF", "Wschód"),
    ("NW2_EPG0_SWO_R32_BCF", "Środkowy Zachód"),
    ("NW2_EPG0_SWO_R34_BCF", "Góry Skaliste"),
    ("NW2_EPG0_SWO_R35_BCF", "Pacyfik"),
    ("NW2_EPG0_SWO_R33_BCF", "Południowe Centrum"),
    ("NW2_EPG0_SSO_R33_BCF", "– w tym kawerny solne"),
    ("NW2_EPG0_SNO_R33_BCF", "– w tym pozostałe"),
]
status = {"errors": [], "notes": []}

SEED = """2026-06-26,2922,87,2740,64,2953
2026-07-03,2983,61,2794,54,3006
2026-07-10,3024,41,2838,44,3052
2026-07-17,3056,32,2871,33,3075
2026-07-24,3084,28,2898,27,3123
2026-07-31,3117,33,2917,19,3130
2026-08-07,3153,36,2951,34,3186
2026-08-14,3169,16,2981,29,3199
2026-08-21,3184,15,3013,32,3217
2026-08-28,3214,30,3047,34,3262
2026-09-04,3254,40,3097,50,3333
2026-09-11,3298,44,3170,73,3423
2026-09-18,3351,53,3243,73,3499
2026-09-25,3415,64,3321,78,3551
2026-10-02,3500,85,3420,99,3631
2026-10-09,3567,67,3509,89,3712
2026-10-16,3644,77,3602,93,3798
2026-10-23,3724,80,3676,73,3872
2026-10-30,3801,77,3745,70,3905
2026-11-06,3862,61,3787,40,3954
2026-11-13,3882,20,3802,15,3948"""  # dotychczasowa historia z panelu (średnie 5-letnie wg EIA/mediów)
WNGSR = "https://ir.eia.gov/ngs/wngsr.json"
NAMES = {"total lower 48 states": "Razem Lower 48", "east region": "Wschód", "midwest region": "Środkowy Zachód", "mountain region": "Góry Skaliste",
         "pacific region": "Pacyfik", "south central region": "Południowe Centrum", "south central salt region": "– w tym kawerny solne",
         "south central nonsalt region": "– w tym pozostałe"}
MONS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
WH = ["week_ending", "stocks_bcf", "net_change_bcf", "avg5_stocks_bcf", "avg5_change_bcf", "year_ago_stocks_bcf", "basis"]
RH = ["release_date", "next_release", "week_ending", "region", "stocks_bcf", "net_change_bcf", "avg5_stocks_bcf", "pct_vs_5yr", "year_ago_stocks_bcf", "pct_vs_year_ago"]


def read_csv(name):
    p = os.path.join(D, name)
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def weekly_rows():
    rows = [r for r in read_csv("storage_weekly.csv") if not r.get("basis", "").startswith("EIA API")]
    if not rows:
        rows = [dict(zip(WH, l.split(",") + ["historia z panelu"])) for l in SEED.splitlines()]
    return {r["week_ending"]: r for r in rows}


def official():
    """Raport EIA z pliku wngsr.json (oficjalne średnie 5-letnie, bez klucza API)."""
    j = requests.get(WNGSR, params={"t": str(int(datetime.now().timestamp()))}, headers={"Cache-Control": "no-cache", "User-Agent": "Mozilla/5.0 (gwhdd-dashboard)"}, timeout=60)
    j = json.loads(j.content.decode("utf-8-sig"))  # plik EIA ma znacznik BOM na początku
    y, m, d = j["release_date"][:11].split("-")
    rd = date(int(y), MONS[m], int(d)).isoformat()
    wk = j["current_week"][:10]
    reg, tot = [], None
    for s in j["series"]:
        c = s["calculated"]
        cur = next((x[1] for x in s["data"] if x[0] == j["current_week"]), None)
        ya = next((x[1] for x in s["data"] if x[0] == j["year_ago"]), None)
        name = NAMES.get(s["name"].lower(), s["name"])
        reg.append([rd, next_release(wk), wk, name, cur, c.get("net_change"), c.get("5yr-avg"), c.get("pct-chg_5yr-avg"), ya if ya is not None else "", c.get("pct-change_yrago")])
        if s["name"].lower() == "total lower 48 states":
            tot = {"stocks": cur, "net": c.get("net_change"), "avg5": c.get("5yr-avg"), "ya": ya}
    if tot is None:
        raise RuntimeError("wngsr.json: brak sumy Lower 48")
    return rd, wk, reg, tot




def write_csv(name, header, rows):
    with open(os.path.join(D, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def fetch():
    if not KEY:
        raise RuntimeError("brak sekretu EIA_API_KEY")
    start = (date.today() - timedelta(days=7 * 52 * 7)).isoformat()
    params = [("api_key", KEY), ("frequency", "weekly"), ("data[0]", "value"), ("start", start),
              ("sort[0][column]", "period"), ("sort[0][direction]", "asc"), ("offset", "0"), ("length", "5000")]
    params += [("facets[series][]", s) for s, _ in SERIES]
    r = requests.get(URL, params=params, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"EIA API HTTP {r.status_code}: {r.text[:200]}")
    data = r.json().get("response", {}).get("data", [])
    if not data:
        raise RuntimeError("EIA API: brak danych")
    out = {s: {} for s, _ in SERIES}
    for x in data:
        if x.get("series") in out and x.get("value") not in (None, ""):
            out[x["series"]][x["period"][:10]] = float(x["value"])
    return out


def near(series, d, k):
    """Wartość z tego samego tygodnia k lat wcześniej (±3 dni)."""
    t = date.fromisoformat(d) - timedelta(days=364 * k)
    best = None
    for off in range(-3, 4):
        x = (t + timedelta(days=off)).isoformat()
        if x in series and (best is None or abs(off) < abs(best[0])):
            best = (off, series[x])
    return None if best is None else best[1]


def prev_week(series, d):
    ds = sorted(x for x in series if x < d)
    return series[ds[-1]] if ds else None


def stats(series, d):
    cur = series[d]
    p = prev_week(series, d)
    chg = None if p is None else cur - p
    avg_s, avg_c = [], []
    for k in range(1, 6):
        v = near(series, d, k)
        if v is None:
            continue
        avg_s.append(v)
        t = (date.fromisoformat(d) - timedelta(days=364 * k)).isoformat()
        ds = sorted(x for x in series if abs((date.fromisoformat(x) - date.fromisoformat(t)).days) <= 3)
        if ds:
            pv = prev_week(series, ds[0])
            if pv is not None:
                avg_c.append(series[ds[0]] - pv)
    a5 = sum(avg_s) / len(avg_s) if len(avg_s) == 5 else None
    c5 = sum(avg_c) / len(avg_c) if len(avg_c) == 5 else None
    ya = near(series, d, 1)
    return cur, chg, a5, c5, ya


def next_release(week_end):
    """Zwykle czwartek 6 dni po tygodniu raportu; w tygodniu Święta Dziękczynienia środa."""
    d = date.fromisoformat(week_end) + timedelta(days=13)   # czwartek następnego raportu
    tg = date(d.year, 11, 1) + timedelta(days=(3 - date(d.year, 11, 1).weekday()) % 7 + 21)
    if d == tg:
        d -= timedelta(days=1)
    return d.isoformat()


def save_weekly(W):
    rows = sorted(W.values(), key=lambda r: r["week_ending"])[-30:]
    write_csv("storage_weekly.csv", WH, [[r.get(k, "") for k in WH] for r in rows])


if __name__ == "__main__":
    status["started"] = datetime.now(timezone.utc).isoformat()
    p = os.path.join(D, "storage_status.json")
    old = json.load(open(p)) if os.path.exists(p) else {}
    W = weekly_rows()
    try:
        try:
            rd, wk, reg, tot = official()
            src = "raport EIA (wngsr.json)"
        except Exception as e:
            status["notes"].append(f"wngsr.json niedostępny ({str(e)[:120]}) – używam API EIA")
            S = fetch()
            ser = S["NW2_EPG0_SWO_R48_BCF"]
            wk = max(ser)
            cur, chg, a5, c5, ya = stats(ser, wk)
            rd = datetime.now(timezone.utc).date().isoformat()
            tot = {"stocks": cur, "net": chg, "avg5": a5, "ya": ya}
            reg = []
            for s_, name in SERIES:
                if wk in S[s_]:
                    c_, ch_, a_, _, y_ = stats(S[s_], wk)
                    pc = lambda a, b: "" if b in (None, 0) else round(100.0 * (a - b) / b, 1)
                    reg.append([rd, next_release(wk), wk, name, c_, ch_, "" if a_ is None else round(a_), pc(c_, a_), "" if y_ is None else y_, pc(c_, y_)])
            src = "API EIA (średnia 5-letnia przybliżona)"
        if old.get("week_ending") == wk and wk in W and old.get("source", "").startswith("raport EIA"):
            print("bez nowego raportu:", wk)
            raise SystemExit(0)
        prevk = (date.fromisoformat(wk) - timedelta(days=7)).isoformat()
        a5c = ""
        if tot["avg5"] not in (None, "") and prevk in W and W[prevk].get("avg5_stocks_bcf") not in (None, ""):
            a5c = f"{float(tot['avg5']) - float(W[prevk]['avg5_stocks_bcf']):.0f}"
        f0 = lambda v: "" if v in (None, "") else f"{float(v):.0f}"
        W[wk] = {"week_ending": wk, "stocks_bcf": f0(tot["stocks"]), "net_change_bcf": f0(tot["net"]), "avg5_stocks_bcf": f0(tot["avg5"]),
                 "avg5_change_bcf": a5c, "year_ago_stocks_bcf": f0(tot["ya"]), "basis": src}
        save_weekly(W)
        write_csv("storage_regions.csv", RH, [["" if v is None else v for v in r] for r in reg])
        net = None if tot["net"] in (None, "") else float(tot["net"])
        status.update({"week_ending": wk, "release_date": rd, "source": src, "stocks_bcf": tot["stocks"], "net_change_bcf": net,
                       "avg5_change_bcf": None if a5c == "" else float(a5c), "vs_avg5_bcf": None if (net is None or a5c == "") else net - float(a5c),
                       "next_release": next_release(wk), "at": datetime.now(timezone.utc).isoformat()})
    except SystemExit:
        raise
    except Exception as e:
        status["errors"].append(str(e)[:300])
        for k in ("week_ending", "release_date", "source", "stocks_bcf", "net_change_bcf", "avg5_change_bcf", "vs_avg5_bcf", "next_release", "at"):
            if k in old:
                status[k] = old[k]
    json.dump(status, open(p, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(status, ensure_ascii=False, indent=1))
