"""Odczyt GWHDD USA (Open-Meteo), cen gazu (Yahoo, EIA) i szacunku popytu na ogrzewanie – GitHub Actions.
Wersja 2: wagi punktów pogodowych liczone ze zużycia gazu do ogrzewania w stanach (EIA, gospodarstwa domowe
i usługi, bez letniego zużycia bazowego) oraz przeliczenie GWHDD na popyt w Bcf/d z efektem weekendowym.
Wyniki trafiają do folderu data/, skąd zadanie Claude wstawia je do dashboardu."""
import csv, json, os, re, time, calendar
from datetime import datetime, timedelta, timezone, date
import requests

D = "data"
os.makedirs(D, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (gwhdd-dashboard)"
TZ = "America/New_York"
T0 = time.time()
NORMALS_BUDGET_S = 110 * 60   # ile czasu jednego uruchomienia wolno poświęcić na liczenie brakujących norm
status = {"errors": [], "notes": []}

# Stare 23 miasta (wersja 1) – zapasowo, gdy nowe wagi lub normy nie są jeszcze gotowe.
OLD = [["Nowy Jork",40.71,-74.01,520],["Newark NJ",40.74,-74.17,370],["Boston",42.36,-71.06,330],["Filadelfia",39.95,-75.17,230],["Pittsburgh",40.44,-80.0,180],["Waszyngton",38.9,-77.04,330],["Chicago",41.88,-87.63,590],["Detroit",42.33,-83.05,470],["Columbus OH",39.96,-83.0,420],["Indianapolis",39.77,-86.16,200],["Milwaukee",43.04,-87.91,210],["Minneapolis",44.98,-93.27,230],["Kansas City",39.1,-94.58,220],["Omaha",41.26,-95.94,150],["Nashville",36.16,-86.78,170],["Atlanta",33.75,-84.39,200],["Dallas",32.78,-96.8,200],["Houston",29.76,-95.37,180],["Denver",39.74,-104.99,200],["Salt Lake City",40.76,-111.89,130],["Seattle",47.61,-122.33,180],["San Francisco",37.77,-122.42,250],["Los Angeles",34.05,-118.24,380]]

# Wersja 2: stan, punkt, szer., dł., udział punktu w zużyciu stanu (przybliżony, wg ludności).
POINTS = [
 ["AL","Birmingham",33.52,-86.81,1],["AZ","Phoenix",33.45,-112.07,1],["AR","Little Rock",34.75,-92.29,1],
 ["CA","Los Angeles",34.05,-118.24,.45],["CA","San Francisco",37.77,-122.42,.25],["CA","Sacramento",38.58,-121.49,.15],["CA","San Diego",32.72,-117.16,.15],
 ["CO","Denver",39.74,-104.99,1],["CT","Hartford",41.76,-72.68,1],["DE","Wilmington DE",39.74,-75.55,1],["DC","Waszyngton",38.9,-77.04,1],
 ["FL","Orlando",28.54,-81.38,1],["GA","Atlanta",33.75,-84.39,1],["ID","Boise",43.62,-116.2,1],
 ["IL","Chicago",41.88,-87.63,.75],["IL","Springfield IL",39.8,-89.64,.25],["IN","Indianapolis",39.77,-86.16,1],["IA","Des Moines",41.59,-93.62,1],
 ["KS","Wichita",37.69,-97.34,1],["KY","Louisville",38.25,-85.76,1],["LA","Nowy Orlean",29.95,-90.07,1],["ME","Portland ME",43.66,-70.26,1],
 ["MD","Baltimore",39.29,-76.61,1],["MA","Boston",42.36,-71.06,1],["MI","Detroit",42.33,-83.05,.65],["MI","Grand Rapids",42.96,-85.67,.35],
 ["MN","Minneapolis",44.98,-93.27,1],["MS","Jackson",32.3,-90.18,1],["MO","St. Louis",38.63,-90.2,.55],["MO","Kansas City",39.1,-94.58,.45],
 ["MT","Billings",45.78,-108.5,1],["NE","Omaha",41.26,-95.94,1],["NV","Las Vegas",36.17,-115.14,1],["NH","Manchester NH",42.99,-71.46,1],
 ["NJ","Newark NJ",40.74,-74.17,1],["NM","Albuquerque",35.08,-106.65,1],
 ["NY","Nowy Jork",40.71,-74.01,.6],["NY","Buffalo",42.89,-78.88,.2],["NY","Albany",42.65,-73.75,.2],
 ["NC","Charlotte",35.23,-80.84,1],["ND","Fargo",46.88,-96.79,1],
 ["OH","Columbus OH",39.96,-83.0,.4],["OH","Cleveland",41.5,-81.69,.35],["OH","Cincinnati",39.1,-84.51,.25],
 ["OK","Oklahoma City",35.47,-97.52,1],["OR","Portland OR",45.52,-122.68,1],["PA","Filadelfia",39.95,-75.17,.5],["PA","Pittsburgh",40.44,-80.0,.5],
 ["RI","Providence",41.82,-71.41,1],["SC","Columbia SC",34.0,-81.03,1],["SD","Sioux Falls",43.55,-96.73,1],["TN","Nashville",36.16,-86.78,1],
 ["TX","Dallas",32.78,-96.8,.45],["TX","Houston",29.76,-95.37,.4],["TX","San Antonio",29.42,-98.49,.15],
 ["UT","Salt Lake City",40.76,-111.89,1],["VT","Burlington",44.48,-73.21,1],["VA","Richmond",37.54,-77.44,1],
 ["WA","Seattle",47.61,-122.33,.75],["WA","Spokane",47.66,-117.43,.25],["WV","Charleston WV",38.35,-81.63,1],
 ["WI","Milwaukee",43.04,-87.91,.6],["WI","Madison",43.07,-89.4,.4],["WY","Cheyenne",41.14,-104.82,1]]
COMM_WEEKEND_DROP = 0.12   # założenie: w weekend popyt sektora usług niższy o ok. 12%


def get(url, tries=6, wait=65):
    for a in range(tries):
        try:
            r = S.get(url, timeout=180)
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


def key(lat, lon):
    return f"{lat},{lon}"


# ---------- normy ERA5 1996–2025, zapisywane per punkt ----------
def load_normals(coords):
    p2 = os.path.join(D, "normals_v2.json")
    N = json.load(open(p2)) if os.path.exists(p2) else {}
    p1 = os.path.join(D, "normals.json")
    if os.path.exists(p1):  # przenieś normy z wersji 1
        old = json.load(open(p1))
        for c, n in zip(OLD, old):
            if n and len(n) >= 360:
                N.setdefault(key(c[1], c[2]), n)
    for lat, lon in coords:
        k = key(lat, lon)
        if k in N and len(N[k]) >= 360:
            continue
        if time.time() - T0 > NORMALS_BUDGET_S:
            break
        try:
            j = get(f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date=1996-01-01&end_date=2025-12-31&daily=temperature_2m_max,temperature_2m_min&temperature_unit=fahrenheit&timezone={TZ}", tries=8).json()
        except Exception as e:
            status["errors"].append(f"normy {k}: {e}")
            break
        acc = {}
        for t, mx, mn in zip(j["daily"]["time"], j["daily"]["temperature_2m_max"], j["daily"]["temperature_2m_min"]):
            h = hdd(mx, mn)
            if h is not None:
                acc.setdefault(t[5:], []).append(h)
        N[k] = {md: round(sum(v) / len(v), 2) for md, v in acc.items()}
        json.dump(N, open(p2, "w"))
        time.sleep(20)
    missing = [k for k in (key(a, b) for a, b in coords) if not (k in N and len(N[k]) >= 360)]
    return N, missing


# ---------- zużycie gazu w stanach (EIA) ----------
def eia_states():
    p = os.path.join(D, "eia_states.json")
    if os.path.exists(p):
        c = json.load(open(p))
        if (datetime.now(timezone.utc) - datetime.fromisoformat(c["fetched_at"])).days < 180 and len(c["states"]) >= 40:
            return c
    states = sorted({pt[0] for pt in POINTS})
    out = {}
    for st in states:
        rec = {}
        for sector, code in (("res", "3010"), ("com", "3020")):
            try:
                html = get(f"https://www.eia.gov/dnav/ng/hist/n{code}{st.lower()}2m.htm", tries=3, wait=10).text
            except Exception as e:
                status["errors"].append(f"EIA {st} {sector}: {e}")
                continue
            years = {}
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
                cells = [re.sub(r"<[^>]+>|&nbsp;", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]
                if len(cells) >= 13 and re.fullmatch(r"\d{4}", cells[0]):
                    vals = []
                    for v in cells[1:13]:
                        v = v.replace(",", "")
                        vals.append(float(v) if re.fullmatch(r"-?\d+(\.\d+)?", v) else None)
                    years[cells[0]] = vals
            rec[sector] = years
            time.sleep(1)
        if rec.get("res") and rec.get("com"):
            out[st] = rec
    c = {"fetched_at": datetime.now(timezone.utc).isoformat(), "states": out}
    if len(out) >= 40:
        json.dump(c, open(p, "w"))
    return c


def build_weights(eia):
    """Waga stanu = roczne zużycie (gospodarstwa domowe + usługi) minus letnie zużycie bazowe (12 × średnia lipiec–sierpień)."""
    st = eia["states"]
    full = lambda v: v and all(x is not None for x in v)
    years = sorted({y for s in st.values() for y, v in s["res"].items() if full(v)})
    Y = next((y for y in reversed(years) if sum(1 for s in st.values() if full(s["res"].get(y)) and full(s["com"].get(y))) >= 40), None)
    if not Y:
        raise RuntimeError("brak pełnego roku danych EIA")
    heat, info = {}, {}
    res_tot = com_tot = 0.0
    for code, s in st.items():
        r, c = s["res"].get(Y), s["com"].get(Y)
        if not (full(r) and full(c)):
            continue
        tot = [a + b for a, b in zip(r, c)]
        annual = sum(tot)
        base = 12 * (tot[6] + tot[7]) / 2
        heat[code] = max(annual - base, 0.1 * annual)
        info[code] = (annual, heat[code])
        res_tot += sum(r)
        com_tot += sum(c)
    pts = []
    for p in POINTS:
        if p[0] in heat:
            pts.append(p + [heat[p[0]] * p[4]])
    tot_w = sum(p[5] for p in pts)
    for p in pts:
        p[5] = p[5] / tot_w
    return Y, pts, info, com_tot / (res_tot + com_tot)


def demand_model(eia, Y, pts, N, comm_share):
    """Regresja miesięcznego zużycia (gosp. domowe + usługi, stany z wag, średnia 5 lat) względem normalnego GWHDD danego miesiąca."""
    st = eia["states"]
    codes = {p[0] for p in pts}
    yrs = [str(int(Y) - k) for k in range(5)]
    xs, ys = [], []
    for m in range(12):
        vals = []
        for y in yrs:
            tot = 0.0
            ok = True
            for c in codes:
                r, cc = st[c]["res"].get(y), st[c]["com"].get(y)
                if not r or not cc or r[m] is None or cc[m] is None:
                    ok = False
                    break
                tot += r[m] + cc[m]
            if ok:
                vals.append(tot / 1000 / calendar.monthrange(int(y), m + 1)[1])  # Bcf/d
        if not vals:
            continue
        days = [f"{m + 1:02d}-{d:02d}" for d in range(1, calendar.monthrange(2025, m + 1)[1] + 1)]
        g = sum(sum(p[5] * N[key(p[2], p[3])][md] for p in pts) for md in days) / len(days)
        xs.append(g)
        ys.append(sum(vals) / len(vals))
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    base = my - slope * mx
    ss = sum((y - base - slope * x) ** 2 for x, y in zip(xs, ys))
    st_ = sum((y - my) ** 2 for y in ys)
    wd = 7 / (5 + 2 * (1 - comm_share * COMM_WEEKEND_DROP))
    we = wd * (1 - comm_share * COMM_WEEKEND_DROP)
    return {"base_bcfd": round(base, 3), "slope_bcfd_per_gwhdd": round(slope, 4), "r2": round(1 - ss / st_, 3),
            "comm_share": round(comm_share, 3), "weekday_factor": round(wd, 4), "weekend_factor": round(we, 4),
            "eia_year": Y, "years": f"{yrs[-1]}–{yrs[0]}"}


# ---------- prognozy i rzeczywiste GWHDD ----------
def weather(pts, N, version):
    """pts: lista [etykieta, szer., dł., waga] z wagami sumującymi się do 1."""
    W = [p[3] for p in pts]
    LAT = ",".join(str(p[1]) for p in pts)
    LON = ",".join(str(p[2]) for p in pts)
    NN = [N[key(p[1], p[2])] for p in pts]
    runs = {}
    for lab, host, m in [("ECMWF", "api", "ecmwf_ifs025"), ("GFS", "api", "ncep_gfs025"), ("ICON", "api", "dwd_icon"), ("EPS", "ensemble-api", "ecmwf_ifs025_ensemble"), ("GEFS", "ensemble-api", "ncep_gefs05"), ("GEPS", "ensemble-api", "cmc_gem_geps")]:
        try:
            ts = get(f"https://{host}.open-meteo.com/data/{m}/static/meta.json", tries=2, wait=5).json()["last_run_initialisation_time"]
            runs[lab] = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %HZ")
        except Exception:
            runs[lab] = ""
    runs["GEM"] = ""
    runs["NORMA"] = "ERA5 1996-2025"
    # zmiana wag = nowa seria: historia od zera, żeby porównania między odczytami były spójne
    vfile = os.path.join(D, "weights_version.txt")
    old_version = open(vfile).read().strip() if os.path.exists(vfile) else "v1"
    old = read_csv("history.csv") if old_version == version else []
    if old_version != version:
        status["notes"].append(f"nowe wagi ({version}) – historia odczytów zaczęta od nowa")
    open(vfile, "w").write(version)
    last = max((r["snapshot"] for r in old), default=None)
    old_runs = {r["model"]: r["run"] for r in old if r["snapshot"] == last}
    same = last is not None and all(old_runs.get(m) == runs.get(m) and runs.get(m) for m in ["EPS", "GEFS", "ECMWF", "GFS"])
    if same:  # brak nowych przebiegów: pomiń ciężkie pobieranie prognoz, odśwież tylko obserwacje
        status["new_runs"] = False
        status["runs"] = runs
        status["snapshot"] = last
        observed(LAT, LON, W, NN, pts)
        return
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
    S_["NORMA"] = [[NN[i].get(d[5:]) for d in dates] for i in range(len(pts))]
    snap = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")

    status["new_runs"] = not same
    status["runs"] = runs
    win = [5, 6, 7, 8, 9]
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
        crow = []
        for m in ["EPS", "GEFS", "ECMWF", "GFS"]:
            for i, p in enumerate(pts):
                v = [S_[m][i][j] for j in win]
                nv = [S_["NORMA"][i][j] for j in win]
                if None in v or None in nv:
                    continue
                h, n = sum(v), sum(nv)
                crow.append([snap, f"{dates[5]}..{dates[9]}", p[0], f"{W[i] * 100:.1f}", m, f"{h:.1f}", f"{n:.1f}", f"{h - n:.1f}"])
        write_csv("cities.csv", ["snapshot", "window", "city", "weight_pct", "model", "hdd", "normal", "dev"], crow)
        dev = {}
        for m in ["EPS", "GEFS", "ECMWF", "GFS"]:
            try:
                g = sum(sum(W[i] * S_[m][i][j] for i in range(len(pts))) for j in win)
                n = sum(sum(W[i] * S_["NORMA"][i][j] for i in range(len(pts))) for j in win)
                dev[m] = round(g - n, 1)
            except TypeError:
                pass
        status["dev_6_10"] = dev
    observed(LAT, LON, W, NN, pts)


def observed(LAT, LON, W, NN, pts):
    ob = get(f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&daily=temperature_2m_max,temperature_2m_min&past_days=14&forecast_days=1&temperature_unit=fahrenheit&timezone={TZ}").json()
    orow = []
    for k, d in enumerate(ob[0]["daily"]["time"]):
        vals = [hdd(l["daily"]["temperature_2m_max"][k], l["daily"]["temperature_2m_min"][k]) for l in ob]
        if None in vals:
            continue
        nv = [NN[i].get(d[5:]) for i in range(len(pts))]
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
    months = []
    y, m = today.year, today.month
    for k in range(-1, 4):
        mm = (m - 1 + k) % 12 + 1
        yy = y + (m - 1 + k) // 12
        months.append((yy, mm))
    cons = [(f"NG{CODES[mm - 1]}{str(yy)[2:]}", last_trade(yy, mm)) for yy, mm in months]
    front = next(i for i, (_, lt) in enumerate(cons) if lt >= today)
    pick = cons[front:front + 2]   # wygasłe kontrakty nie są już notowane w Yahoo; ich historia zostaje w pliku
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


def main_weather():
    """Wagi wg EIA (v2), a gdy czegoś brakuje – stare 23 miasta (v1)."""
    try:
        eia = eia_states()
        Y, pts, info, comm = build_weights(eia)
        N, missing = load_normals([(p[2], p[3]) for p in pts])
        if missing:
            raise RuntimeError(f"normy jeszcze liczone ({len(pts) - len(missing)}/{len(pts)} punktów) – na razie stare wagi")
        dm = demand_model(eia, Y, pts, N, comm)
        write_csv("weights.csv", ["point", "state", "lat", "lon", "weight_pct", "state_annual_mmcf", "state_heating_mmcf"],
                  [[f"{p[1]} ({p[0]})", p[0], p[2], p[3], f"{p[5] * 100:.2f}", f"{info[p[0]][0]:.0f}", f"{info[p[0]][1]:.0f}"] for p in sorted(pts, key=lambda q: -q[5])])
        write_csv("demand_params.csv", list(dm.keys()), [list(dm.values())])
        status["weights"] = f"v2 (EIA {Y}, {len(pts)} punktów)"
        status["demand"] = dm
        weather([[f"{p[1]} ({p[0]})", p[2], p[3], p[5]] for p in pts], N, "v2")
    except Exception as e:
        status["notes"].append(str(e))
        N, _ = load_normals([(c[1], c[2]) for c in OLD])
        tot = sum(c[3] for c in OLD)
        status["weights"] = "v1 (23 miasta, wagi przybliżone)"
        weather([[c[0], c[1], c[2], c[3] / tot] for c in OLD], N, "v1")


if __name__ == "__main__":
    status["started"] = datetime.now(timezone.utc).isoformat()
    try:
        main_weather()
    except Exception as e:
        status["errors"].append(f"pogoda: {e}")
    try:
        prices()
    except Exception as e:
        status["errors"].append(f"ceny: {e}")
    status["finished"] = datetime.now(timezone.utc).isoformat()
    json.dump(status, open(os.path.join(D, "status.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(status, ensure_ascii=False, indent=1))
