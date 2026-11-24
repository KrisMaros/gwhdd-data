"""Fundamenty tygodniowe dla dashboardu GWHDD (GitHub Actions):
- pozycje funduszy (managed money) w kontrakcie NYMEX Henry Hub NG z raportu CFTC Commitments of Traders,
- zasilanie terminali eksportowych LNG (średnia tygodniowa) z EIA Natural Gas Weekly Update.
Wyniki: data/cot.csv, data/lng.csv, data/fund_status.json."""
import csv, json, os, re
from datetime import datetime, timezone
import requests

D = "data"
os.makedirs(D, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (gwhdd-dashboard)"
status = {"errors": [], "notes": []}

COT_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"  # Disaggregated, Futures Only
NG_CODE = "023651"  # NAT GAS NYME (Henry Hub, NYMEX)
NGWU_URL = "https://www.eia.gov/naturalgas/weekly/"


def read_csv(name):
    p = os.path.join(D, name)
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name, header, rows):
    with open(os.path.join(D, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def num(r, *keys):
    for k in keys:
        if k in r and r[k] not in (None, ""):
            return float(r[k])
    raise KeyError(keys[0])


def cot():
    q = {"cftc_contract_market_code": NG_CODE, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "170"}
    r = S.get(COT_URL, params=q, timeout=60)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("CFTC: brak wierszy dla kodu " + NG_CODE)
    out = []
    for x in rows:
        d = x["report_date_as_yyyy_mm_dd"][:10]
        lo = num(x, "m_money_positions_long_all", "m_money_positions_long")
        sh = num(x, "m_money_positions_short_all", "m_money_positions_short")
        oi = num(x, "open_interest_all", "open_interest")
        out.append([d, int(lo), int(sh), int(lo - sh), int(oi)])
    out.sort()
    nets = [o[3] for o in out]
    rows_w = []
    for i, o in enumerate(out):
        hist = nets[max(0, i - 155):i + 1]   # ok. 3 lata
        pct = 100.0 * sum(1 for v in hist if v <= o[3]) / len(hist)
        chg = o[3] - out[i - 1][3] if i else ""
        rows_w.append([o[0], o[1], o[2], o[3], chg, o[4], f"{100.0 * o[3] / o[4]:.1f}", f"{pct:.0f}"])
    write_csv("cot.csv", ["report_date", "mm_long", "mm_short", "mm_net", "mm_net_change", "open_interest", "net_pct_oi", "net_percentile_3y"], rows_w)
    last = rows_w[-1]
    status["cot"] = {"report_date": last[0], "mm_net": last[3], "mm_net_change": last[4], "percentile_3y": int(last[7]), "market": rows[0].get("market_and_exchange_names", "")}


MON = {m: i + 1 for i, m in enumerate("January February March April May June July August September October November December".split())}


def lng():
    html = S.get(NGWU_URL, timeout=60).text
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"week ending (?:\w+, )?(\w+) (\d{1,2}), (\d{4})", text)
    if not m or m.group(1) not in MON:
        raise RuntimeError("EIA NGWU: nie znaleziono daty tygodnia")
    week = datetime(int(m.group(3)), MON[m.group(1)], int(m.group(2))).date().isoformat()
    age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(week).date()).days
    if age > 21:
        # strona EIA nie była aktualizowana – nie zapisujemy starych danych, a stary wiersz usuwamy
        rows = [r for r in read_csv("lng.csv") if r["week_ending"] != week]
        write_csv("lng.csv", ["week_ending", "feedgas_bcfd", "source"], [[r["week_ending"], r["feedgas_bcfd"], r["source"]] for r in rows])
        status["lng_stale"] = week
        raise RuntimeError(f"EIA NGWU nieaktualny (ostatnie wydanie: tydzień do {week})")
    val = None
    for v in re.finditer(r"averag\w*\s+(?:of\s+)?(\d{1,2}\.\d)\s*Bcf/d", text):
        before = text[max(0, v.start() - 250):v.start()]
        if "LNG" in before and re.search(r"deliver|feed ?gas|receipt", before, re.I) and 5 <= float(v.group(1)) <= 40:
            val = float(v.group(1))
            break
    if val is None:
        raise RuntimeError("EIA NGWU: nie znaleziono średniego zasilania terminali LNG")
    rows = {r["week_ending"]: r for r in read_csv("lng.csv")}
    rows[week] = {"week_ending": week, "feedgas_bcfd": f"{val:.1f}", "source": "EIA Natural Gas Weekly Update"}
    srt = sorted(rows.values(), key=lambda r: r["week_ending"])[-104:]
    write_csv("lng.csv", ["week_ending", "feedgas_bcfd", "source"], [[r["week_ending"], r["feedgas_bcfd"], r["source"]] for r in srt])
    status["lng"] = {"week_ending": week, "feedgas_bcfd": val}


if __name__ == "__main__":
    status["started"] = datetime.now(timezone.utc).isoformat()
    for name, fn in (("COT", cot), ("LNG", lng)):
        try:
            fn()
        except Exception as e:
            status["errors"].append(f"{name}: {e}"[:300])
    old = {}
    p = os.path.join(D, "fund_status.json")
    if os.path.exists(p):
        old = json.load(open(p))
    # zachowaj ostatnie dobre wartości, gdy źródło chwilowo nie odpowiada
    for k in ("cot", "lng"):
        if k not in status and k in old and not (k == "lng" and old[k].get("week_ending") == status.get("lng_stale")):
            status[k] = old[k]
    changed = any(status.get(k) != old.get(k) for k in ("cot", "lng"))
    status["at"] = datetime.now(timezone.utc).isoformat() if changed or "at" not in old else old["at"]
    json.dump(status, open(p, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(status, ensure_ascii=False, indent=1))
