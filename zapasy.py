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


if __name__ == "__main__":
    status["started"] = datetime.now(timezone.utc).isoformat()
    p = os.path.join(D, "storage_status.json")
    old = json.load(open(p)) if os.path.exists(p) else {}
    try:
        S = fetch()
        tot = S["NW2_EPG0_SWO_R48_BCF"]
        last = max(tot)
        if old.get("week_ending") == last:
            print("bez nowego raportu:", last)
            raise SystemExit(0)
        rows = []
        for d in sorted(tot)[-26:]:
            cur, chg, a5, c5, ya = stats(tot, d)
            r = lambda v: "" if v is None else f"{v:.0f}"
            rows.append([d, r(cur), r(chg), r(a5), r(c5), r(ya), "EIA API; średnia 5-letnia z historii EIA (±3 dni)"])
        write_csv("storage_weekly.csv", ["week_ending", "stocks_bcf", "net_change_bcf", "avg5_stocks_bcf", "avg5_change_bcf", "year_ago_stocks_bcf", "basis"], rows)
        today = datetime.now(timezone.utc).date().isoformat()
        nr = next_release(last)
        reg = []
        for s, name in SERIES:
            if last not in S[s]:
                status["errors"].append(f"brak {name} dla {last}")
                continue
            cur, chg, a5, c5, ya = stats(S[s], last)
            pct = lambda a, b: "" if b in (None, 0) else f"{100.0 * (a - b) / b:.1f}"
            reg.append([today, nr, last, name, f"{cur:.0f}", "" if chg is None else f"{chg:.0f}", "" if a5 is None else f"{a5:.0f}", pct(cur, a5), "" if ya is None else f"{ya:.0f}", pct(cur, ya)])
        write_csv("storage_regions.csv", ["release_date", "next_release", "week_ending", "region", "stocks_bcf", "net_change_bcf", "avg5_stocks_bcf", "pct_vs_5yr", "year_ago_stocks_bcf", "pct_vs_year_ago"], reg)
        cur, chg, a5, c5, ya = stats(tot, last)
        status.update({"week_ending": last, "stocks_bcf": cur, "net_change_bcf": chg, "avg5_change_bcf": None if c5 is None else round(c5),
                       "vs_avg5_bcf": None if (chg is None or c5 is None) else round(chg - c5), "next_release": nr,
                       "at": datetime.now(timezone.utc).isoformat()})
    except SystemExit:
        raise
    except Exception as e:
        status["errors"].append(str(e)[:300])
        for k in ("week_ending", "stocks_bcf", "net_change_bcf", "avg5_change_bcf", "vs_avg5_bcf", "next_release", "at"):
            if k in old:
                status[k] = old[k]
    json.dump(status, open(p, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(status, ensure_ascii=False, indent=1))
