"""
Walk results/, read run.json + hdbtcount_*.log for every bp-*GiB iteration,
compute steady-state TPM stats, emit data/runs.json.

Primary per-second TPM source is HammerDB's own `hdbtcount_*.log` (one sample
per second while the measurement window is open). Falls back to qps.csv or
tpm_1sec.csv when the hdbtcount log is missing.

The series is trimmed:
  - leading rampup is skipped (driven by run.json::benchmark.rampup_minutes)
  - trailing ramp-down (last consecutive samples < 10% of steady-state median)
    is stripped, so the benchmark's natural tail-off doesn't pollute the mean
    or the timeseries chart.
"""
import csv, json, re, statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
OUT = ROOT / "data" / "runs.json"


def engine_id(profile: str, version: str) -> str | None:
    p = (profile or "").lower()
    v = (version or "").lower()
    if p == "maria-12" or v.startswith("12.2"):
        return "maria122"
    if p == "maria-12.3" or v.startswith("12.3"):
        return "maria123"
    if p == "9.7" or v.startswith("9.7"):
        return "mysql97"
    if p == "8.4" or v.startswith("8.4"):
        return "mysql84"
    return None


def load_run_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ── hdbtcount parser ─────────────────────────────────────────────────────────
HDBT_LINE_RE = re.compile(r"^(\d+)\s+(?:MySQL|MariaDB)\s+tpm\s+@\s+(.+)$")
# HammerDB logs timestamps like "Fri Apr 24 10:26:38 UTC 2026".
HDBT_TS_FMT = "%a %b %d %H:%M:%S %Z %Y"


def _parse_hdbt_ts(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, HDBT_TS_FMT)
    except ValueError:
        return None


def read_hdbtcount_tpm_series(iter_dir: Path, rampup_sec: int) -> list[float]:
    """Return steady-state per-second TPM values parsed from hdbtcount_*.log.

    Gaps (when HammerDB drops a sample) are filled by repeating the previous
    value — the `hdbtcount` log is dense enough (<1% drops in practice) that
    this has negligible effect on percentile statistics and keeps the per-second
    index aligned with wall-clock seconds.
    """
    logs = sorted(iter_dir.glob("hdbtcount_*.log"))
    if not logs:
        return []
    # One log per iteration in practice; if multiple, concatenate in name order.
    parsed: list[tuple[datetime, float]] = []
    for path in logs:
        for line in path.read_text().splitlines():
            m = HDBT_LINE_RE.match(line)
            if not m:
                continue
            ts = _parse_hdbt_ts(m.group(2))
            if ts is None:
                continue
            parsed.append((ts, float(m.group(1))))
    if not parsed:
        return []
    t0 = parsed[0][0]
    # Build a dict keyed by integer elapsed seconds, then walk densely.
    by_sec: dict[int, float] = {}
    for ts, tpm in parsed:
        sec = int((ts - t0).total_seconds())
        by_sec[sec] = tpm  # last value wins for duplicates
    max_sec = max(by_sec)
    vals: list[float] = []
    last = 0.0
    for sec in range(max_sec + 1):
        if sec < rampup_sec:
            continue
        v = by_sec.get(sec)
        if v is None:
            v = last  # gap-fill with previous sample
        else:
            last = v
        vals.append(v)
    return vals


# ── fallback: derive TPM from TPS in qps.csv / tpm_1sec.csv ──────────────────
def read_tpm_series_fallback(iter_dir: Path, rampup_sec: int) -> list[float]:
    """Fallback per-second TPM series when hdbtcount_*.log is missing."""
    qps = iter_dir / "qps.csv"
    if qps.exists() and qps.stat().st_size > 0:
        rows = list(csv.DictReader(qps.open()))
        if rows and "tps" in rows[0]:
            t0 = datetime.fromisoformat(rows[0]["timestamp"])
            vals: list[float] = []
            for r in rows:
                try:
                    secs = (datetime.fromisoformat(r["timestamp"]) - t0).total_seconds()
                except Exception:
                    continue
                if secs < rampup_sec:
                    continue
                try:
                    vals.append(float(r["tps"]) * 60.0)
                except (ValueError, TypeError):
                    pass
            return vals

    tpm = iter_dir / "tpm_1sec.csv"
    if tpm.exists() and tpm.stat().st_size > 0:
        vals = []
        for r in csv.DictReader(tpm.open()):
            try:
                s = int(r["second"])
                if s < rampup_sec:
                    continue
                vals.append(float(r["tpm"]))
            except (ValueError, TypeError, KeyError):
                pass
        return vals
    return []


# ── ramp-down trim ───────────────────────────────────────────────────────────
def trim_rampdown(vals: list[float], fraction: float = 0.10) -> list[float]:
    """Drop trailing samples that fall below `fraction` of the median.

    Keeps the last stable measurement but removes the HammerDB graceful shutdown
    tail (e.g. `… 328860, 70260, 0`), which otherwise drags the rolling-average
    line off the right edge of the chart.
    """
    if not vals:
        return vals
    nz = [v for v in vals if v > 0]
    if not nz:
        return vals
    median = statistics.median(nz)
    threshold = median * fraction
    end = len(vals)
    while end > 0 and vals[end - 1] < threshold:
        end -= 1
    return vals[:end]


# ── TEST RESULT parser (unchanged) ───────────────────────────────────────────
TEST_RESULT_RE = re.compile(
    r"System achieved\s+(\d+)\s+NOPM\s+from\s+(\d+)\s+(MySQL|MariaDB)\s+TPM",
    re.IGNORECASE,
)


def parse_hammerdb_result(iter_dir: Path) -> dict | None:
    """Return {'nopm': int, 'tpm': int} from HammerDB's TEST RESULT line, or None."""
    cli_out = iter_dir / "hammerdbcli.out"
    if not cli_out.exists() or cli_out.stat().st_size == 0:
        return None
    text = cli_out.read_text(errors="replace")
    matches = TEST_RESULT_RE.findall(text)
    if not matches:
        return None
    nopm_str, tpm_str, _engine = matches[-1]
    return {"nopm": int(nopm_str), "tpm": int(tpm_str)}


# ── summarize ────────────────────────────────────────────────────────────────
def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p)))
    return sorted_vals[k]


def summarize(vals: list[float]) -> dict:
    if not vals:
        return {}
    vals_nz = [v for v in vals if v > 0]
    if not vals_nz:
        return {}
    s = sorted(vals_nz)
    mean = statistics.fmean(vals_nz)
    std = statistics.pstdev(vals_nz) if len(vals_nz) > 1 else 0.0
    return {
        "avg": round(mean, 2),
        "min": round(s[0], 2),
        "max": round(s[-1], 2),
        "p5":  round(percentile(s, 0.05), 2),
        "p25": round(percentile(s, 0.25), 2),
        "median": round(percentile(s, 0.50), 2),
        "p75": round(percentile(s, 0.75), 2),
        "p95": round(percentile(s, 0.95), 2),
        "std": round(std, 2),
        "cv_pct": round(std / mean * 100, 2) if mean else 0.0,
        "samples": len(vals_nz),
    }


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    runs = []
    for run_dir in sorted(RESULTS.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith("vu-sweep"):
            continue
        for bp_dir in sorted(run_dir.iterdir()):
            if not bp_dir.is_dir() or not bp_dir.name.startswith("bp-"):
                continue
            rj = bp_dir / "run.json"
            if not rj.exists():
                continue
            meta = load_run_json(rj)
            if not meta:
                continue
            b = meta.get("benchmark", {})
            db = meta.get("database", {})
            innodb = meta.get("innodb", {})
            host = meta.get("host", {})
            rampup_sec = int(b.get("rampup_minutes", 10)) * 60

            tpm_vals = read_hdbtcount_tpm_series(bp_dir, rampup_sec)
            source = "hdbtcount"
            if not tpm_vals:
                tpm_vals = read_tpm_series_fallback(bp_dir, rampup_sec)
                source = "qps" if tpm_vals else "none"
            tpm_vals = trim_rampdown(tpm_vals)

            hdb_result = parse_hammerdb_result(bp_dir)
            eid = engine_id(db.get("profile"), db.get("version"))
            m = re.search(r"(\d+)", bp_dir.name)
            bp_gib = int(m.group(1)) if m else innodb.get("buffer_pool_size_gib")

            record = {
                "run_dir": run_dir.name,
                "iter": bp_dir.name,
                "bp_gib": bp_gib,
                "vu": b.get("num_virtual_users"),
                "warehouses": b.get("warehouses"),
                "rampup_min": b.get("rampup_minutes"),
                "duration_min": b.get("duration_minutes"),
                "engine_id": eid,
                "profile": db.get("profile"),
                "version": db.get("version"),
                "image": db.get("image"),
                "bp_instances": innodb.get("buffer_pool_instances"),
                "timestamp_utc": meta.get("timestamp_utc"),
                "cpu_count": host.get("cpu_count"),
                "ram_gib": host.get("ram_gib"),
                "kernel": host.get("kernel"),
                "tpm_source": source,
                "tpm": summarize(tpm_vals),
                "tpm_series": [round(v, 2) for v in tpm_vals],
                "hammerdb_reported": hdb_result,
            }
            runs.append(record)
            hdb_tpm = hdb_result["tpm"] if hdb_result else "—"
            avg = record["tpm"].get("avg", "?")
            print(f"  {run_dir.name}/{bp_dir.name}  eid={eid}  vu={record['vu']}  "
                  f"bp={bp_gib}G  source={source}  avg_tpm={avg}  "
                  f"n={record['tpm'].get('samples', 0)}  hdb_tpm={hdb_tpm}")

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w") as f:
        json.dump(runs, f, indent=2)
    print(f"\nWrote {len(runs)} iterations -> {OUT}")


if __name__ == "__main__":
    main()
