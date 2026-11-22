"""Odczyt GWHDD USA (Open-Meteo) i cen gazu (Yahoo, EIA) – uruchamiany przez GitHub Actions.
Wyniki trafiają do folderu data/, skąd pobiera je zadanie Claude i wstawia do dashboardu."""
import csv, io, json, os, re, time
from datetime import datetime, timedelta, timezone, date
import requests

D = "data"
os.makedirs(D, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (gwhdd-dashboard)"
TZ = "America/New_York"
CITIES = [["NYC","Nowy Jork",40.71,-74.01,520],["Newark","Newark NJ",40.74,-74.17,370],["Boston","Boston",42.36,-71.06,330],["Philadelphia","Filadelfia",39.95,-75.17,230],["Pittsburgh","Pittsburgh",40.44,-80.0,180],["Washington","Waszyngton",38.9,-77.04,330],["Chicago","Chicago",41.88,-87.63,590],["Detroit","Detroit",42.33,-83.05,470],["Columbus","Columbus OH",39.96,-83.0,420],["Indianapolis","Indianapolis",39.77,-86.16,200],["Milwaukee","Milwaukee",43.04,-87.91,210],["Minneapolis","Minneapolis",44.98,-93.27,230],["KansasCity","Kansas City",39.1,-94.58,220],["Omaha","Omaha",41.26,-95.94,150],["Nashville","Nashville",36.16,-86.78,170],["Atlanta","Atlanta",33.75,-84.39,200],["Dallas","Dallas",32.78,-96.8,200],["Houston","Houston",29.76,-95.37,180],["Denver","Denver",39.74,-104.99,200],["SaltLake","Salt Lake City",40.76,-111.89,130],["Seattle","Seattle",47.61,-122.33,180],["SanFrancisco","San Francisco",37.77,-122.42,250],["LosAngeles","Los Angeles",34.05,-118.24,380]]
TOT = sum(c[4] for c in CITIES)
W = [c[4] / TOT for c in CITIES]
LAT = ",".join(str(c[2]) for c in CITIES)
LON = ",".join(str(c[3]) for c in CITIES)
status = {"errors": []}


def get(url, tries=6, wait=65):
    for a in range(tries):
        try:
            r = S.get(url, timeout=120)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait)
                continue
            r.raise_for_status()
        except requests.RequestException:
            if a == tries - 1:
                raise
            time.sleep(wait)
    raise RuntimeError("limit zapytań: " + url[:80])


def hdd(mx, mn):
    return None if mx is None or mn is None else max(0.0, 65 - (mx + mn) / 2)


def read_csv(name):
    p = os.path.join(D, name)
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name, header, rows):
    with open(os.path.join(D, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


# ---------- normy ERA5 1996–2025 (liczone raz, potem z pliku) ----------
def normals():
    p = os.path.join(D, "normals.json")
    N = json.load(open(p)) if os.path.exists(p) else [None] * len(CITIES)
    for i, c in enumerate(CITIES):
        if N[i] and len(N[i]) >= 360:
            continue
        j = get(f"https://archive-api.open-meteo.com/v1/archive?latitude={c[2]}&longitude={c[3]}&start_date=1996-01-01&end_date=2025-12-31&daily=temperature_2m_max,temperature_2m_min&temperature_unit=fahrenheit&timezone={TZ}").json()
        acc = {}
        for t, mx, mn in zip(j["daily"]["time"], j["daily"]["temperature_2m_max"], j["daily"]["temperature_2m_min"]):
            h = hdd(mx, mn)
            if h is not None:
                acc.setdefault(t[5:], []).append(h)
        N[i] = {md: round(sum(v) / len(v), 2) for md, v in acc.items()}
        json.dump(N, open(p, "w"))
        time.sleep(20)
    return N


# ---------- prognozy i rzeczywiste GWHDD ----------
def weather(N):
    S_ = {}
    fc = get(f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&daily=temperature_2m_max,temperature_2m_min&models=ecmwf_ifs025,gfs_seamless,gem_seamless,icon_seamless&temperature_unit=fahrenheit&forecast_days=16&timezone={TZ}").json()
    dates = fc[0]["daily"]["time"]
    for lab, m in [("ECMWF", "ecmwf_ifs025"), ("GFS", "gfs_seamless"), ("GEM", "gem_seamless"), ("ICON", "icon_seamless")]:
        S_[lab] = [[hdd(l["daily"]["temperature_2m_max_" + m][k], l["daily"]["temperature_2m_min_" + m][k]) for k in range(len(dates))] for l in fc]
    for lab, m in [("EPS", "ecmwf_ifs025"), ("GEFS", "gfs05"), ("GEPS", "gem_global")]:
        j = get(f"https://ensemble-api.open-meteo.com/v1/ensemble?latitude={LAT}&longitude={LON}&hourly=temperature_2m&models={m}&temperature_unit=fahrenheit&forecast_days=16&timezone={TZ}").json()
        res = []
        for loc in j:
            t = loc["hourly"]["time"]
            keys = [k for k in loc["hourly"] if k.startswith("temperature_2m")]
            row = []
            for d in dates:
                idx = [i for i, x in enumerate(t) if x.startswith(d)]
                s = n = 0
                for k in keys:
                    v = [loc["hourly"][k][i] for i in idx if loc["hourly"][k][i] is not None]
                    if len(v) < 8:
                        continue
                    s += max(0.0, 65 - (max(v) + min(v)) / 2)
                    n += 1
                row.append(s / n if n else None)
            res.append(row)
        S_[lab] = res
    S_["NORMA"] = [[N[i].get(d[5:]) for d in dates] for i in range(len(CITIES))]
    runs = {}
    for lab, host, m in [("ECMWF", "api", "ecmwf_ifs025"), ("GFS", "api", "ncep_gfs025"), ("ICON", "api", "dwd_icon"), ("EPS", "ensemble-api", "ecmwf_ifs025_ensemble"), ("GEFS", "ensemble-api", "ncep_gefs05"), ("GEPS", "ensemble-api", "cmc_gem_geps")]:
        try:
            ts = get(f"https://{host}.open-meteo.com/data/{m}/static/meta.json", tries=2, wait=5).json()["last_run_initialisation_time"]
            runs[lab] = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %HZ")
        except Exception:
            runs[lab] = ""
    runs["GEM"] = ""
    runs["NORMA"] = "ERA5 1996-2025"
    snap = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")

    # historia: dopisz nowy odczyt, chyba że przebiegi 4 głównych modeli są te same co w ostatnim
    old = read_csv("history.csv")
    last = max((r["snapshot"] for r in old), default=None)
    old_runs = {r["model"]: r["run"] for r in old if r["snapshot"] == last}
    same = last is not None and all(old_runs.get(m) == runs.get(m) and runs.get(m) for m in ["EPS", "GEFS", "ECMWF", "GFS"])
    status["new_runs"] = not same
    status["runs"] = runs
    if not same:
        new = []
        for m, s in S_.items():
            for k, d in enumerate(dates):
                v = [x[k] for x in s]
                if any(x is None for x in v):
                    continue
                new.append([snap, m, runs.get(m, ""), d, f"{sum(W[i] * x for i, x in enumerate(v)):.2f}"])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%MZ")
        rows = [[r["snapshot"], r["model"], r["run"], r["date"], r["gwhdd"]] for r in old if r["snapshot"] >= cutoff] + new
        write_csv("history.csv", ["snapshot", "model", "run", "date", "gwhdd"], rows)
        status["snapshot"] = snap
        # miasta, dni 6–10
        win = [5, 6, 7, 8, 9]
        crow = []
        for m in ["EPS", "GEFS", "ECMWF", "GFS"]:
            for i, c in enumerate(CITIES):
                v = [S_[m][i][j] for j in win]
                nv = [S_["NORMA"][i][j] for j in win]
                if None in v or None in nv:
                    continue
                h, n = sum(v), sum(nv)
                crow.append([snap, f"{dates[5]}..{dates[9]}", c[1], f"{W[i] * 100:.1f}", m, f"{h:.1f}", f"{n:.1f}", f"{h - n:.1f}"])
        write_csv("cities.csv", ["snapshot", "window", "city", "weight_pct", "model", "hdd", "normal", "dev"], crow)
        # odchylenie dni 6–10 do opisu w dzienniku
        dev = {}
        for m in ["EPS", "GEFS", "ECMWF", "GFS"]:
            try:
                g = sum(sum(W[i] * S_[m][i][j] for i in range(len(CITIES))) for j in win)
                n = sum(sum(W[i] * S_["NORMA"][i][j] for i in range(len(CITIES))) for j in win)
                dev[m] = round(g - n, 1)
            except TypeError:
                pass
        status["dev_6_10"] = dev
    else:
        status["snapshot"] = last

    ob = get(f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&daily=temperature_2m_max,temperature_2m_min&past_days=14&forecast_days=1&temperature_unit=fahrenheit&timezone={TZ}").json()
    orow = []
    for k, d in enumerate(ob[0]["daily"]["time"]):
        vals = [hdd(l["daily"]["temperature_2m_max"][k], l["daily"]["temperature_2m_min"][k]) for l in ob]
        if None in vals:
            continue
        nv = [N[i].get(d[5:]) for i in range(len(CITIES))]
        norm = f"{sum(W[i] * x for i, x in enumerate(nv)):.2f}" if None not in nv else ""
        orow.append([d, f"{sum(W[i] * x for i, x in enumerate(vals)):.2f}", norm])
    write_csv("gwhdd_obs.csv", ["date", "gwhdd_obs", "gwhdd_norm"], orow)


# ---------- ceny ----------
CODES = "FGHJKMNQUVXZ"


def last_trade(y, m):
    d = date(y, m, 1)
    n = 0
    while n < 3:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


def yahoo(sym, rng):
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d", tries=3, wait=10).json()["chart"]["result"][0]
    out = []
    for t, c in zip(j.get("timestamp", []), j["indicators"]["quote"][0]["close"]):
        if c is not None:
            dt = datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=5)
            out.append((dt.strftime("%Y-%m-%d"), round(c, 3)))
    return out, j["meta"]


def prices():
    today = datetime.now(timezone.utc).date()
    # kontrakty: dwa ostatnie wygasłe, najbliższy i następny
    months = []
    y, m = today.year, today.month
    for k in range(-2, 4):
        mm = (m - 1 + k) % 12 + 1
        yy = y + (m - 1 + k) // 12
        months.append((yy, mm))
    cons = [(f"NG{CODES[mm - 1]}{str(yy)[2:]}", last_trade(yy, mm)) for yy, mm in months]
    front = next(i for i, (_, lt) in enumerate(cons) if lt >= today)
    pick = cons[front:front + 2]
    cp = {(r["date"], r["contract"]): r for r in read_csv("contract_prices.csv")}
    for code, lt in pick:
        try:
            rows, _ = yahoo(code + ".NYM", "3mo")
            for d, c in rows:
                if d <= lt.isoformat():
                    cp[(d, code)] = {"date": d, "contract": code, "close": f"{c:.3f}", "source": f"Yahoo {code}.NYM"}
        except Exception as e:
            status["errors"].append(f"{code}: {e}")
    write_csv("contract_prices.csv", ["date", "contract", "close", "source"], sorted(([r["date"], r["contract"], r["close"], r["source"]] for r in cp.values())))

    pr = {r["date"]: r for r in read_csv("prices.csv")}
    for r in pr.values():
        r["note"] = ""
    try:
        rows, meta = yahoo("NG=F", "1mo")
        for d, c in rows:
            pr.setdefault(d, {"date": d, "ng_front": "", "hh_spot": "", "note": ""})["ng_front"] = f"{c:.3f}"
        live = datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc)
        pl = live + timedelta(hours=1)  # czas polski zimowy
        if rows:
            pr[rows[-1][0]]["note"] = f"na żywo {pl:%Y-%m-%d %H:%M} PL"
        status["ng_front"] = meta.get("regularMarketPrice")
        status["ng_front_at"] = live.isoformat()
    except Exception as e:
        status["errors"].append(f"NG=F: {e}")
    try:
        html = get("https://www.eia.gov/dnav/ng/hist/rngwhhdD.htm", tries=3, wait=10).text
        mon = {k: i + 1 for i, k in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
            cells = [re.sub(r"<[^>]+>|&nbsp;", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]
            if not cells:
                continue
            mt = re.match(r"(\d{4}) ?([A-Z][a-z]{2})-\s*(\d+)", cells[0])
            if not mt:
                continue
            d0 = date(int(mt.group(1)), mon[mt.group(2)], int(mt.group(3)))
            if d0 < today - timedelta(days=45):
                continue
            for i in range(1, 6):
                v = cells[i] if i < len(cells) else ""
                if v:
                    d = (d0 + timedelta(days=i - 1)).isoformat()
                    pr.setdefault(d, {"date": d, "ng_front": "", "hh_spot": "", "note": ""})["hh_spot"] = v
    except Exception as e:
        status["errors"].append(f"Henry Hub: {e}")
    cutoff = (today - timedelta(days=60)).isoformat()
    write_csv("prices.csv", ["date", "ng_front", "hh_spot", "note"], sorted([r["date"], r.get("ng_front", ""), r.get("hh_spot", ""), r.get("note", "")] for r in pr.values() if r["date"] >= cutoff))


if __name__ == "__main__":
    status["started"] = datetime.now(timezone.utc).isoformat()
    try:
        weather(normals())
    except Exception as e:
        status["errors"].append(f"pogoda: {e}")
    try:
        prices()
    except Exception as e:
        status["errors"].append(f"ceny: {e}")
    status["finished"] = datetime.now(timezone.utc).isoformat()
    json.dump(status, open(os.path.join(D, "status.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(status, ensure_ascii=False, indent=1))
