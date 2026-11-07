# LAN historical routes GeoPackage v3 (endpoint evidence), 2026-07-14 → 2026-09-30

How to build `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg` from scratch: LATAM flown
routes from ADS-B Exchange traces, cut into flights between airports as before. Then every other
input that exists improves the endpoints and adds the flights ADS-B missed:
- ACARS position reports (POS) fill coverage holes, carry flight ends to their airports, and give
  each flight its number;
- the aircraft's own sequence of flights (rotation) is used;
- the OFP planned legs are used.

Every path, file and command needed to reproduce it is below.

- **Tool:** `adsb_historical_routes.py` 1.5.0 (branch `endpoint-evidence`), standard-library
  Python only.
- **Output schema:** `flown_routes` v3: v2 plus each endpoint's source, candidates and confidence,
  flight numbers and POS-only flights. See [`docs/GEOPACKAGE.md`](docs/GEOPACKAGE.md) §7.
- **Request:** [`docs/handoff/2026-11-03_endpoint_evidence_v3.md`](docs/handoff/2026-11-03_endpoint_evidence_v3.md).
- **Fleets:**
  - The 175 ICAO hexes in `lan_active_tails_icao.csv`, all LATAM Brasil (JJ) registrations
    (PR-, PS-, PT-), have ADS-B and POS.
  - With `--pos-fleets all`, the other LATAM fleets in the POS feed (CC-, HC-, N-) get POS-only
    flights.
  - Everything is recorded under the airline code `LAN`.
- **v3.1 (2026-11-07):** `2026_0714-0930_v3.1.gpkg` is the same build, made with commit
  `15d53d9` or later. The only difference is `endpoint_confidence`: an end confirmed by high
  evidence (observed, FlightAware, or the OFP on flight number) is no longer demoted to `low`
  because an earlier rescue weighed several airports. 4,136 flights move from low to high; every
  other column and row is identical. Use v3.1. To build it, run §6 with
  `--output $OUT/2026_0714-0930_v3.1.gpkg`; its files carry the `_v3.1` stem. The v3 file stays
  as it was.
- **The 1.4.0 guide** ([`historical_route_geopackage_instructions_lan.md`](historical_route_geopackage_instructions_lan.md))
  and its file `2026_0714-0930.gpkg` stay as they are. That file is direct_detect's baseline:
  never overwrite it.

---

## 1. What you need

| # | Input | Path | Notes |
|---|---|---|---|
| 1 | ADS-B traces (daily KML per aircraft) | `/Volumes/jy-ext/kml/kml-raw/lan_2026_jul-sept/` | 13,475 files `<hex>_<YYYY-MM-DD>_baro_avg.kml`, 175 hexes, **2026-07-01 → 2026-09-15**, 3.1 GB. Ignore the `runs/` subfolder (downloader logs). |
| 2 | Airports | `~/Git/kml/adsb_historical_routes/input/lan_202606-202607_airports.csv` | 118 airports, `airport,latitude,longitude,elevation_ft`. |
| 2a | Airports in the OFPs but not in 2 (F7) | `~/Git/kml/adsb_historical_routes/input/lan_ofp_extra_airports.csv` | 6 airports: SGAS, SDSC, SCNT, LFBO, SBSJ, SPZO. Same columns; how to make it is in §1.2. Not committed (data). |
| 3 | Route times | `~/Git/kml/adsb_historical_routes/input/lan_202606-202607_routes_time.csv` | 481 routes, `origin,destination,avg_enroute_min`. |
| 4 | ACARS POS reports | `/Volumes/jy-ext/data_lan/trial_analysis/pos/` | One SQS JSON message per report, all LATAM fleets: 2,074,428 files (download completed 2026-10-30); 1,005,029 reports parse for the 175 hexes and 1,069,398 for 202 other registrations. |
| 5 | Tail → ICAO hex map | `~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv` | `tail_number,icao_hex`, 175 rows. Needed to key POS reports (by registration) to the ADS-B traces (by hex). |
| 6 | Cirium schedule (only to rebuild 2 and 3) | `~/Git/kml/adsb_historical_routes/input/lan_route_cirium_202606-2020607.csv` | 1,876 rows, June and July 2026, marketing carrier LA (1,506 rows operated by JJ). The file name has a typo, `2020607`. |
| 7 | navdb (only for the QGIS project, §9.2) | `~/Git/qgis/input/navdb_2610.gpkg` | AIRAC 2610 chart layers: airports, airways, fixes, FIRs, SIDs/STARs. |
| 8 | Company routes (optional, §9) | `/Volumes/jy-ext/company_gpkg/lan/company_route_lan_2026_0507-0908.gpkg` | `company_routes` v2 from `company_route_extractor`: 9,444 variants, all LATAM carriers, OFPs 2026-05-07 → 09-08. |
| 9 | **Planned legs (OFP)** | `/Volumes/jy-ext/data_lan/trial_analysis/planned_legs/lan_ofp_legs_2026-07-01_2026-09-08.csv` | 124,390 legs, latest plan per flight, all LATAM carriers, scheduled 2026-07-01 → 2026-09-08. Written by direct_detect (`python -m direct_detect.ingest.planned_legs`). Columns in §5.1. |

Software:

- Python 3.7 or later (built and tested with 3.9.6). No packages are needed to build.
- `pytest` to run the tests (optional).
- QGIS 3.44 (`/Applications/QGIS.app`) to validate and view (optional). It provides `ogrinfo` and
  GDAL's `validate_gpkg.py`.
- Disk and memory: see §6 (measured).

### 1.1 Where the inputs came from

- **Traces.** Downloaded with `~/Git/kml/adsb_kml_downloader` from the 177 tails of
  `lan_active_tails_icao.csv`, `baro_avg` altitude, 2026-07-01 → 2026-09-15. The run logs are in
  the trace folder's `runs/`. To extend the range, run the downloader again for the missing dates
  into the same folder; it skips files that already exist.
- **Airports and route times.** Built from the Cirium CSV with `build_cirium_inputs.py` (§3).
  Route times use the larger of the June and July average block times.
- **POS.** LATAM's ACARS feed (`prod-rest-intake-queue.fifo`), saved one message per file as
  `<TAIL>_raw_<HHMMSS>_<uuid>.json`.
- **Planned legs.** direct_detect's export of the auth OFPs (latest plan per flight). `tail` is
  upper case without a hyphen. `ambiguous = 1` marks an uncertain tail or identity; there are none
  in this file.

### 1.2 The six extra airports (F7)

The OFPs of the covered tails end at six airports missing from the Cirium list. These are SGAS
(230 endpoints), SDSC (49), SCNT (22), LFBO, SBSJ and SPZO (1 each). Make the file from
`airport_lookup`'s OurAirports cache:

```bash
cd ~/Git/kml/adsb_historical_routes
python3 - <<'EOF'
import csv
want = {"SGAS", "SDSC", "SCNT", "LFBO", "SBSJ", "SPZO"}
rows = {r["ident"]: r for r in csv.DictReader(open(
    "/Users/johnyoung/Git/conversion_tools/airport_lookup/.airport_cache/airports.csv"))
    if r["ident"] in want}
with open("input/lan_ofp_extra_airports.csv", "w") as f:
    f.write("airport,latitude,longitude,elevation_ft\n")
    for k in sorted(want):
        r = rows[k]
        f.write(f"{k},{float(r['latitude_deg']):.6f},{float(r['longitude_deg']):.6f},"
                f"{int(float(r['elevation_ft']))}\n")
EOF
cat input/lan_ofp_extra_airports.csv
```

```
airport,latitude,longitude,elevation_ft
LFBO,43.629101,1.363820,499
SBSJ,-23.229200,-45.861500,2120
SCNT,-51.670670,-72.529078,217
SDSC,-21.875032,-47.903851,2649
SGAS,-25.240156,-57.519227,292
SPZO,-13.535700,-71.938797,10860
```

`--airports` takes both files. A code in the later file would replace the same code in the
earlier one; none overlap here.

---

## 2. Get the code

```bash
cd ~/Git/kml/adsb_historical_routes
git fetch origin
git checkout endpoint-evidence
git pull
python3 --version                                  # 3.7+ (tested 3.9.6)
python3 -m pytest -q                               # optional: 268 passed, 1 skipped
```

---

## 3. (Optional) Rebuild airports and route times from Cirium

The files in `input/` are already built. Rebuild them only if the Cirium extract changes, for
example to add August and September.

```bash
cd ~/Git/kml/adsb_historical_routes
python3 build_cirium_inputs.py \
    --input  input/lan_route_cirium_202606-2020607.csv \
    --output input/lan_202606-202607
cat input/lan_202606-202607_error.txt               # must say: VERDICT: READY
```

This writes `input/lan_202606-202607_airports.csv`, `input/lan_202606-202607_routes_time.csv`
and `input/lan_202606-202607_error.txt`. Airport coordinates come from
`~/Git/conversion_tools/airport_lookup`.

---

## 4. Check the inputs

```bash
T=/Volumes/jy-ext/kml/kml-raw/lan_2026_jul-sept
P=/Volumes/jy-ext/data_lan/trial_analysis/pos
MAP=~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv
cd ~/Git/kml/adsb_historical_routes

# Traces: file count, date range, aircraft
ls $T | grep -c '\.kml$'                                          # 13475
ls $T | grep '\.kml$' | sed -E 's/^[0-9a-f]+_([0-9-]+)_.*/\1/' | sort | sed -n '1p;$p'
                                                                  # 2026-07-01 / 2026-09-15
ls $T | grep '\.kml$' | cut -d_ -f1 | sort -u | wc -l              # 175

# Airports / routes
wc -l input/lan_202606-202607_airports.csv input/lan_202606-202607_routes_time.csv   # 119 / 482

# Every trace hex is in the tail map
python3 - <<'EOF'
import csv, os
T = '/Volumes/jy-ext/kml/kml-raw/lan_2026_jul-sept'
m = {r['icao_hex'].strip().lower() for r in csv.DictReader(
    open(os.path.expanduser('~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv'),
         encoding='utf-8-sig'))}
hexes = {f.split('_')[0] for f in os.listdir(T) if f.endswith('.kml')}
print(len(hexes), 'hexes,', len(hexes - m), 'missing from map')   # 175 hexes, 0 missing
EOF
```

### 4.1 Check the POS download

Count the files, and the files per fleet (a listing of over a million files takes about 20 s with
`os.scandir`; `ls` is much slower):

```bash
python3 - <<'EOF'
import os, collections
c = collections.Counter()
with os.scandir('/Volumes/jy-ext/data_lan/trial_analysis/pos') as it:
    for e in it:
        c[e.name[:2]] += 1
print(sum(c.values()), 'files'); print(c.most_common())
EOF
```

Then parse the reports for this fleet and count them per UTC day, so you can see the date
coverage before building. This uses the tool's own parser and reads only the files of mapped
tails (PR-, PS-, PT-), so it takes several minutes:

```bash
cd ~/Git/kml/adsb_historical_routes
python3 - <<'EOF'
import collections, os
import adsb_historical_routes as A
tail_to_hex = A.load_tail_to_hex(os.path.expanduser(
    '~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv'))
idx = A.load_pos_points('/Volumes/jy-ext/data_lan/trial_analysis/pos', tail_to_hex)
per_day = collections.Counter()
for (hexc, day), pts in idx.items():
    per_day[day] += len(pts)
print(sum(per_day.values()), 'POS fixes,', len(idx), 'aircraft-days')
for day in sorted(per_day):
    print(day, per_day[day])
EOF
```

Every day from 2026-07-14 to the last trace day should have reports. A missing or thin day
means the download is incomplete.

---

## 5. The POS format

Each file is an SQS message. The `body` is a raw ACARS (ARINC 620) Airbus-style position report:

```
QU CPYXXXX
.SCLASLA 040037
FML
FI JJ3634/AN PR-MYV
DT QXB GRU1 040037 F83A
-  POSS23156W046116,S23190W046136,003708,216,UBNOV,005054,UMLAM,M16,32722,204/TS003708,08042675CA
```

| Field | Example | Meaning |
|---|---|---|
| `FI` | `JJ3634` | Flight number (IATA prefix). v3 stores it on each fix; `--acars-identity` gives it to the flight. Some reports have none (`FI /AN PT-XPJ`). |
| `/AN` | `PR-MYV` | Registration. It is normalized to `PRMYV` and mapped to the hex through `lan_active_tails_icao.csv`. Unmapped registrations (CC-, HC-, N-) are keyed by the normalized registration. |
| first `POS` coordinate | `S23156W046116` | Present position, DDMM.m: S 23°15.6′ W 046°11.6′. |
| 2nd field | `S23190W046136` or `ITARA` | Last waypoint. Not used. |
| 3rd field | `003708` | Report time, HHMMSS UTC. |
| 4th field | `216` | Flight level; altitude = 21,600 ft. |
| `bucket_path` / `uri` | `…/PRMYV-JJ3634-20260804003719545.txt` | Receive time; it gives the date. |

- **The date.** The report has no reliable date: its trailing date is MMDDYY on some aircraft
  and DDMMYY on others. The loader takes the date from the receive stamp in the S3 key (else the
  SQS `SentTimestamp`). A report time more than an hour after the receive time belongs to the
  previous day.
- **Validation.** Checked against the ADS-B track at the same instant on 7,726 reports: median
  0.33 km, 90th percentile 0.54 km, and 7,716 of 7,726 within 5 km.
- **Altitude.** POS altitude is the reported flight level, a pressure altitude. The ADS-B KML
  altitude reads 3–6 % higher in this region, about 2,000 ft at cruise and close to zero near the
  ground, so POS stretches draw slightly below the ADS-B parts of a track.
- **Files that are read.** With `--pos-fleets all` every file is opened (2,074,428), so the
  other LATAM fleets (CC-, HC-, N…) can get POS-only flights. With `mapped` (or 1.4.0) their files
  are skipped by name. Other ACARS messages (OOOI and so on) are ignored. The folder is listed once,
  with `os.scandir`.

### 5.1 The planned legs

```
leg_key,source,carrier_icao,flight_number,flight_digits,tail,orig,dest,sched_dep_utc,ofp_date,ambiguous
2026-07-14_TAM3036_SBSP-SBCF_b2,ofp,TAM,TAM3036,3036,PRMAG,SBSP,SBCF,2026-07-14T11:00:00Z,2026-07-14,0
```

- OFP flight numbers carry ICAO prefixes (TAM 59,865, LPE 22,381, LAN 14,971, ARE 13,911, …)
  while ACARS `FI` uses IATA ones (`JJ`, `LA`, `4C`, …). Legs match on `flight_digits` with the
  tail and the time, so the prefix never matters. The build learns the prefix table from the
  matches (`meta.flight_number_prefixes`, §13).
- A flight matches a leg when it takes off within `[sched_dep − 60 min, sched_dep + 8 h]`.
  - Rule 1: same tail and same digits as the flight's ACARS number.
  - Rule 2 (flights without a number): same tail and one observed end equal to the leg's.
  - The match must be unique both ways.
- The OFPs end on **2026-09-08**. Flights after that use the plan-free evidence only.

---

## 6. Build

Run it from the repository so the relative input paths resolve. The output folder is created if
it does not exist. The log goes next to the GeoPackage.

```bash
cd ~/Git/kml/adsb_historical_routes
OUT=/Volumes/jy-ext/route_gpkg/lan
mkdir -p $OUT

python3 adsb_historical_routes.py \
    --kml-folder /Volumes/jy-ext/kml/kml-raw/lan_2026_jul-sept \
    --airports   input/lan_202606-202607_airports.csv input/lan_ofp_extra_airports.csv \
    --routes     input/lan_202606-202607_routes_time.csv \
    --confidence permissive --corridor-cross-track-km 50 \
    --integrate-pos true \
    --pos-folder /Volumes/jy-ext/data_lan/trial_analysis/pos \
    --tail-icao  ~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv \
    --acars-identity true --pos-extend-ends true \
    --pos-only-flights true --pos-fleets all \
    --rotation-endpoints true --recover-dropped true \
    --planned-legs /Volumes/jy-ext/data_lan/trial_analysis/planned_legs/lan_ofp_legs_2026-07-01_2026-09-08.csv \
    --stitch-gaps true \
    --airline LAN \
    --takeoff-from 2026-07-14 --takeoff-to 2026-09-30 \
    --format gpkg \
    --output $OUT/2026_0714-0930_v3.gpkg \
    2>&1 | tee $OUT/2026_0714-0930_v3_build.log
```

**Never** point `--output` at `2026_0714-0930.gpkg`: that is the 1.4.0 file and direct_detect's
baseline.

**Try a short window first.** Run the same command with
`--takeoff-from 2026-08-01 --takeoff-to 2026-08-03` and an output in a scratch folder. Check the
four items of §8.1 before the full build. The POS read is the same (about 13 min), and the rest
takes about 2 min.

| Flag | Why |
|---|---|
| `--confidence permissive --corridor-cross-track-km 50` | The Route Builder's recommended preset ("custom" in the outputs), as used for VIV. It rescues flights whose ADS-B starts or ends away from the airport, which is common over Brazil's interior. |
| `--integrate-pos true` + `--pos-folder` + `--tail-icao` | Merges POS fixes into each flight's ADS-B coverage holes. This also turns on the `track_points` table, which records each point's source (`ADSB` / `POS`). F1, F2 and F3 need it. |
| `--airports … lan_ofp_extra_airports.csv` | F7: the six OFP airports missing from the Cirium list (§1.2). |
| `--acars-identity true` | F1: `flights.flight_number` from the POS `FI` field. |
| `--pos-extend-ends true` | F2: carries flight ends ADS-B lost en route on with POS, to the airport when a POS fix is low over it (`pos_observed`). |
| `--pos-only-flights true --pos-fleets all` | F3: flights from POS alone, for the 175 hexes (legs ADS-B missed) and for every other registration in the feed (CC-, HC-, N-). |
| `--rotation-endpoints true` | F4: an end ADS-B did not see takes the aircraft's next (or previous) flight's observed airport. |
| `--recover-dropped true` | F9: segments 1.4.0 drops are completed with the evidence above where possible. |
| `--planned-legs …csv` | F5: the OFP legs confirm or fill ends that were not observed, and the flight number. They never override an observed end. |
| `--stitch-gaps true` | Spec §8.1. Stored in `meta.kml_params`, so a KML rendered later marks the remaining inferred gaps in grey. The GeoPackage geometry is the same either way. |
| `--airline LAN` | Fills `flights.airline` and `meta.airline`. |
| `--takeoff-from 2026-07-14 --takeoff-to 2026-09-30` | Keeps flights whose UTC takeoff date is in range. The traces from 07-01 to 07-12 are not read; 07-13 is read so flights airborne at midnight are completed rather than split. |
| `--format gpkg` | GeoPackage only. Make the KML later from the GeoPackage (§10). |
| `--output …/2026_0714-0930_v3.gpkg` | A name ending in `.gpkg` names the GeoPackage itself. A fixed name is used, not `{from}`/`{to}`. |

**Runtime:** 31 minutes (1,868 s) on the MacBook Pro (16 GB RAM, Python 3.9), with the data on
the USB volume:

| Step | Time |
|---|---|
| Read the POS files (all 2,074,428; 1.0 M fixes for the 175 hexes and 1.07 M for 202 other registrations) | 12 min (718 s) |
| Parse the traces, detect and join flights | ~4 min |
| Evidence stage (F1–F9, every date) | 1 min |
| Write the GeoPackage (26.7 M `track_points` rows plus indexes; 8.6 GB) | 14 min (849 s) |

Peak memory was 3.5 GB resident. Free disk needed: about 9 GB for the file (it is written beside
itself as `.gpkg.part` until complete), plus 70 MB for the diagnostics CSV.

---

## 7. Outputs

Everything is in `/Volumes/jy-ext/route_gpkg/lan/`:

| File | Contents |
|---|---|
| `2026_0714-0930_v3.gpkg` | The GeoPackage: `flights`, `tracks_full`, `track_points`, `diagnostics` and `meta`. |
| `2026_0714-0930_v3_evidence_report.md` | Written by the build: flights by sources and disposition, endpoints by source and confidence, flight numbers and the learned prefix table, POS-only flights by registration prefix, F2 extensions, F9 recoveries, and the agreement table (§8.1). |
| `2026_0714-0930_v3.kml.diagnostics.csv` | One row per raw segment and its outcome, plus `evidence` rows for every endpoint F2/F4/F5 changed. The same data is in the `diagnostics` table. |
| `2026_0714-0930_v3_build.log` | Console log of the run. The run summary is at the end. |
| `2026_0714-0930_v3_summary.txt` | `gpkg_summary.py` output: as 1.4.0, plus the endpoint-evidence sections. |
| `2026_0714-0930_v3_validation.txt` | GDAL `validate_gpkg.py`, `ogrinfo` feature counts, rows per table, and the date-line crossing count (§8). |

The KML goes to the processed-KML folder (§10):
`/Volumes/jy-ext/kml/kml-processed/adsb_historical_routes_output/lan_routes_2026_0714-0930_v3.kml`.

Write the two text files after the build:

```bash
cd ~/Git/kml/adsb_historical_routes
OUT=/Volumes/jy-ext/route_gpkg/lan
python3 gpkg_summary.py $OUT/2026_0714-0930_v3.gpkg -o $OUT/2026_0714-0930_v3_summary.txt --top 25
```

and the validation file with the commands of §8.3, redirected into
`$OUT/2026_0714-0930_v3_validation.txt`.

The CSV keeps the `.kml.diagnostics.csv` suffix even with `--format gpkg`: it is always named
after the KML stem.

---

## 8. Validate

### 8.1 The run summary

The end of `2026_0714-0930_v3_build.log`:

```bash
tail -60 /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3_build.log
```

Check the evidence lines first, as for the short window (request §6.4: a direction, not a target):

1. `1.4.0 inferred ends a rule-1 plan contradicts: {...}`: most of them change
   (`free_now_agrees` + `free_changed_still_differs` > `unchanged`).
2. In the evidence report's agreement table, `rotation` agrees with the plan clearly more often
   than `route_time` and `geometry`.
3. Flight-number coverage is high on ADS-B flights with POS in their window. Check it with
   `gpkg_summary.py` (*Flights with a flight number*), or:
   `sqlite3 $G "SELECT count(*), sum(flight_number_source='acars_fi') FROM flights WHERE sources LIKE 'ADSB%'"`.
4. `F3 POS-only flights:` and the report's prefix table list `CC` flights.

If one of them does not hold, stop and look before using the file. Then the 1.4.0 lines:

- `Takeoff-date filter 2026-07-14 .. 2026-09-30: dropped N flight(s), M remain`
- `Loaded N extra fixes across M aircraft-days`: if N is 0, the POS folder or the tail map is wrong.
- `Position-source integration: filled N real fix(es) into M of K flights`
- `Dropped N duplicate fix(es) and M implausible fix(es)`: duplicates are expected (the feed sends
  each report twice); implausible should be a small number (64 in this build).
- `Flight dates: 2026-07-14 -> …`
- `Suspect O/D (flown > 2.5x great circle): N (p%)`

### 8.2 Query the GeoPackage

```bash
G=/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg
sqlite3 $G "SELECT key, value FROM meta ORDER BY key"
sqlite3 $G "SELECT disposition, count(*) FROM flights GROUP BY 1"
sqlite3 $G "SELECT orig_source, dest_source, count(*) n FROM flights GROUP BY 1, 2 ORDER BY n DESC LIMIT 20"
sqlite3 $G "SELECT endpoint_confidence, count(*) FROM flights GROUP BY 1"
sqlite3 $G "SELECT evidence_feature, evidence_source, count(*) FROM diagnostics WHERE phase = 'evidence' GROUP BY 1, 2"
sqlite3 $G "SELECT count(*), min(flight_date), max(flight_date), sum(suspect_od) FROM flights"
sqlite3 $G "SELECT sources, count(*) FROM flights GROUP BY sources"
sqlite3 $G "SELECT source, count(*) FROM track_points GROUP BY source"
sqlite3 $G "SELECT city_pair, count(*) n FROM flights GROUP BY city_pair ORDER BY n DESC LIMIT 15"
sqlite3 $G "SELECT disposition, count(*) FROM diagnostics GROUP BY disposition"
```

### 8.3 File validity with GDAL (from QGIS)

```bash
G=/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg
QGIS_APP=/Applications/QGIS.app/Contents
export PROJ_DATA=$QGIS_APP/Resources/qgis/proj GDAL_DATA=$QGIS_APP/Resources/qgis/gdal
$QGIS_APP/MacOS/ogrinfo -so -al $G | grep -E "^Layer name|^Feature Count"
$QGIS_APP/MacOS/python \
    $QGIS_APP/Resources/python3.11/site-packages/osgeo_utils/samples/validate_gpkg.py $G
```

`validate_gpkg.py` prints nothing when the file is valid (about 4 minutes for this file). The
`ogrinfo` feature counts must equal the `count(*)` of each table.

### 8.4 QGIS

Add every layer as in §9.1. Nothing should be written to the log panel (*View → Panels → Log
Messages*). Useful layer filters (*Layer Properties → Source → Query Builder*):

- `"suspect_od" = 0`: exclude implausible origin/destination labels from statistics.
- `"city_pair" = 'SBGR-SBRF'`: one route.
- `"disposition" = 'pos_only'`: flights built from POS alone (sparse: minutes between points).
- `"endpoint_confidence" = 'high'`: both ends observed by ADS-B or matched to the OFP on flight number.
- `"registration" LIKE 'CC-%'`: the Chilean-registered fleet (POS only).

---

## 9. Open in QGIS

### 9.1 Add the layers directly (works now)

1. Start QGIS 3.44.
2. Drag `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg` onto the map, or use
   *Layer → Add Layer → Add Vector Layer…* and choose the file.
3. Add `flights`, plus `track_points` to see POS coverage: style it *Categorized* by `source`
   (`ADSB` / `POS`).
4. Add the company routes the same way: `company_routes` from
   `/Volumes/jy-ext/company_gpkg/lan/company_route_lan_2026_0507-0908.gpkg`.
5. Both files are EPSG:4326 and share one map.

*File → Open Project* only opens QGIS projects (`.qgs` / `.qgz`), so a `.gpkg` is greyed out
there. Use *Add Vector Layer* or drag and drop for GeoPackages.

### 9.2 The full Route Explorer project (once the qgis handoff has landed)

The project is built by `~/Git/qgis` `prepare`, which owns the styles, basemaps and navdb chart
layers. It links to the GeoPackages and copies no data (about 70–90 KB). Today's `prepare` cannot
build it yet: it has no `--project-name`, needs `--company`, and accepts flown schema v1 only (v3 is needed).
The call then fails cleanly (exit 1, the GeoPackage untouched) and points to
`~/Git/kml/company_route_extractor/docs/handoff/qgis_prepare_partial_inputs.md`; see also
[`docs/handoff/qgis_flown_v2.md`](docs/handoff/qgis_flown_v2.md).

Once it has landed:

```bash
cd ~/Git/kml/adsb_historical_routes
python3 gpkg_to_qgz.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg \
  --navdb ~/Git/qgis/input/navdb_2610.gpkg \
  --company /Volumes/jy-ext/company_gpkg/lan/company_route_lan_2026_0507-0908.gpkg
```

This writes `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.qgz`. Alternatively, add
`--qgz --navdb ~/Git/qgis/input/navdb_2610.gpkg --company <company file>` to the §6 build.

- **One-time setup:** run `~/Git/qgis/scripts/install_plugin.sh`. Then in QGIS open
  *Plugins → Manage and Install Plugins… → Installed* and tick **Route Explorer**.
- **Open:** *File → Open Project* → `2026_0714-0930_v3.qgz` → the Route Explorer toolbar button.
- **Checks:**

  ```bash
  cd ~/Git/qgis && source scripts/env.sh
  "$QGIS_PY" scripts/smoke_render.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.qgz
  #   exit 0 = within the render-time targets
  sqlite3 /Volumes/jy-ext/route_gpkg/lan/analysis.gpkg \
    "SELECT count(*), count(company_route_key), sum(suspect_od) FROM flight_metrics"
  #   flights, flights matched to a company variant, suspect O/D (VIV matched 7,035 of 7,125)
  ```

### 9.3 Caveats

- **Dates.** The flights are UTC takeoff 2026-07-14 → 2026-09-30 by filter, but the traces end on
  2026-09-15. The company variants run 2026-05-07 → 2026-09-08. `prepare` keeps a variant in force
  for 7 days after its `last_seen`, so flights up to about 2026-09-15 can match.
- **Navdb cycle.** Only AIRAC 2610 (2026-10-01 → 10-29) exists. Every LAN flight is outside it, so
  SID/STAR usage is flagged `navdb_cycle_mismatch` (qgis assumption N-03). Airways and FIRs are
  unaffected.
- **Fleet.** The ADS-B flights cover the 175 LATAM Brasil (JJ) tails; the POS-only flights cover
  the other LATAM fleets too; the company file covers all LATAM carriers. Flights are matched by
  `city_pair`.
- **Schema.** `prepare` must accept flown `schema_version` "3" (the handoff asks for "1" and "2");
  v3 only adds columns, so accepting it is the whole change.
- **Moving files.** The project links its inputs by relative path. Moving or renaming any of them
  breaks it; rebuild the `.qgz` with `gpkg_to_qgz.py` (seconds, no re-parsing).
- **Keys.** The `.qgz` holds the CARTO basemap key. Don't share or commit it.

---

## 10. KML for Google Earth (optional)

Render it from the GeoPackage. Every option defaults to the build settings (`meta.kml_params`):

```bash
cd ~/Git/kml/adsb_historical_routes
mkdir -p /Volumes/jy-ext/kml/kml-processed/adsb_historical_routes_output
python3 gpkg_to_kml.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg \
    -o /Volumes/jy-ext/kml/kml-processed/adsb_historical_routes_output/lan_routes_2026_0714-0930_v3.kml
# altitude-coloured instead:
python3 gpkg_to_kml.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg \
    -o /Volumes/jy-ext/kml/kml-processed/adsb_historical_routes_output/lan_routes_2026_0714-0930_v3_altitude.kml \
    --color-mode altitude
```

POS-only flights of unmapped fleets are named by registration (for example `CC-BMD-SBGR-EBBR-2026-08-24`).

POS stretches are orange (`ff00a5ff`), and remaining inferred gaps are grey (`ff888888`).
The default export took 3.1 minutes and 3.8 GB of memory. It writes a 651 MB KML with 502,472
placemarks for the 97,120 flights at 2-minute sampling (2.80 M of the 26.7 M points). POS-only
flights have a gap between almost every pair of reports, so they are mostly grey connectors
between short orange pieces. For a lighter file, export with `--stitch-gaps false`. Placemark
names are hidden by default (`--labels true` shows them); the description gives the aircraft
(the registration for unmapped fleets), the route, the date and the duration.

---

## 11. Data limits for this dataset

1. **ADS-B ends on 2026-09-15.** The traces were downloaded for 2026-07-01 → 2026-09-15 only.
   POS runs to 09-30, so the JJ fleet's flights of 09-16 → 09-30 are **POS-only**. That is why
   September has 22,269 POS-only flights against 8,100 in July. To get ADS-B for them, download
   those days into the trace folder (§1.1) and rebuild; this is the owner's call
   (`~/Git/kml/adsb_kml_downloader`, a separate project).
2. **Planned legs end on 2026-09-08,** where the OFPs end. Later flights get only the plan-free
   evidence (POS, rotation, route time), so `planned:*` sources stop on 09-08.
3. **No ADS-B for the CC-, HC- and N- fleets.** The downloader has no hexes for them. Their only
   route coverage is POS-only flights (`aircraft_id` null, `registration` set), until hexes are
   added.
4. **99 planned-leg airports are not in the airport list.** They are mostly domestic Colombia
   (SKRG, SKCL, SKCG, …), Peru (SPQU, SPRU, SPHI, …) and Ecuador (SEQM, SEGU, …), plus a few
   US/Central American ones. The Cirium extract covers the network of LATAM *Brasil* only (the
   six F7 airports are added). No flight can end at a missing airport, so:
   - POS-only flights of those fleets that touch one stay unresolved and are dropped (most of the
     38,075 `unresolved` sessions);
   - one could be labelled with a listed airport within 100 km.

   The full list with leg counts is at the end of the evidence report. Adding them needs a Cirium
   extract for all LATAM carriers (§3), or another airports file passed after the first in
   `--airports`. Owner's call.
5. **POS-only flights are sparse.** Reports are a median 6.9 min apart; nothing is interpolated.
   - **Durations:** `duration_min` runs from the first to the last report (mean 101 min, 11.6
     reports). Reports start after takeoff and stop before landing, so durations are short of block
     time, for example SPJC-SPZO 39 min.
   - **Destinations:** the last report is rarely low over the airport, so destinations mostly come
     from rotation (the next flight's origin) or the plan. Their origins are mostly `pos_observed`.
6. **Ten mapped hexes had no flights in 1.4.0.**
   - Five now have POS-only flights: E47EA0 PR-MAG 471, E4994D PT-TMT 451, E480D2 PR-MHQ 429,
     E47EBC PR-MAK 310, E47FA7 PR-MBG 9.
   - Five still have none: E4827E PR-MYC, E49FBE PS-LHA, E49FC1 PS-LHB, E4A243 PS-LHG and E4A288
     PS-LHI.
   - Check all ten hexes against a registry: empty ADS-B traces next to airborne POS point to a
     wrong hex in `lan_active_tails_icao.csv`.
7. **The Cirium inputs are June–July route-level averages,** not dated flight-level schedules.
   Converted to the planned-legs columns (§5.1), a dated flight-level extract would let airlines
   without OFPs use F5.
8. **POS is cleaned as in 1.4.0.** Every report arrives twice; duplicates (14,632) and fixes that
   need more than 1,300 km/h to or from the ADS-B track (64) are dropped. POS altitude is pressure
   altitude, about 2,000 ft below the ADS-B KML at cruise (§5).
9. **`flight_id`s of extended flights differ from 1.4.0.** The id is the aircraft and the first
   point's time. When POS extends a flight's start, its id changes, and one 07-14 00:04Z flight
   now takes off on 07-13 and falls outside the date filter. Match v2 and v3 by aircraft and time
   overlap, not by `flight_id`.
10. **The six F7 airports change a few observed ends.** 28 ADS-B ends are now observed at a nearer
    new airport, for example SBSJ or SDSC. One SDSC→SDSC local flight (LATAM's maintenance base),
    which 1.4.0 called SBSP→SBRP, is now dropped as `invalid`.
11. **122 flights cross the date line.** All are LATAM Chile 787 POS-only flights across the
    Pacific (YSSY-SCEL 40, NZAA-SCEL 26, SCEL-YMML 9, …). They are stored as two-part lines,
    split at ±180 (schema v2 rule), and draw correctly in QGIS. No ADS-B flight crosses.
12. **`rotation_chained` ends are weak.** They agree with the plan only 37% (origins) and 52%
    (destinations) of the time.
    - They occur only on POS-only flights (1,311 origins, 815 destinations) and recovered segments
      (475, 121).
    - Example: a trans-Pacific flight whose first report is already over the ocean gets its origin
      from the previous session's inferred destination, giving "SCIE-YSSY" or "YMML-YSSY".
    - All such flights are `endpoint_confidence = low`. Filter on confidence, or on
      `*_source <> 'rotation_chained'`, for route statistics.

---

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Loaded 0 extra fixes` | The wrong `--pos-folder`, a missing `--tail-icao`, or an older tool version that only reads the `<Airline>_Position=` format. Check `TOOL_VERSION` in `adsb_historical_routes.py` is 1.5.0 for this guide. |
| `Error: --acars-identity, ... need --integrate-pos true and --pos-folder` | F1, F2 and F3 read POS: add `--integrate-pos true --pos-folder …`. |
| `Error: planned-legs file not found` | The `--planned-legs` path is wrong or `/Volumes/jy-ext` is not mounted. |
| `Warning: N planned-leg airport(s) are not in the airport list` | Expected here (Data limits, item 4). The list shows which airports the plans use but no flight can end at. |
| `unrecognized arguments: --acars-identity` | A 1.4.0 checkout: `git checkout endpoint-evidence`. |
| `Warning: POS folder given but no tail<->hex map` | `--tail-icao` is missing or its path is wrong. |
| `Error: Cannot create output directory` | `/Volumes/jy-ext` is not mounted. |
| `Error: No KML files left inside the takeoff date range` | The trace folder has no files dated within a day of 2026-07-14 … 2026-09-30. |
| Many flights rescued to, or dropped near, one place that is not in the airports file | That airport is missing. Add it to the Cirium extract, or to `airport_overrides.csv`, and rebuild (§3). |
| The `.gpkg.part` file is left behind | The run was interrupted. Delete it and run again; the GeoPackage is only renamed into place when complete. |

---

## 13. Results of this build

Built 2026-11-03 (tool 1.5.0, `meta.created_utc` 2026-11-04T00:03:17Z). Compared with the 1.4.0
file `2026_0714-0930.gpkg` built from the same traces and POS:

| Measure | 1.4.0 (v2) | 1.5.0 (v3) |
|---|---:|---:|
| Flights | 51,345 | **97,120** |
| …ADS-B flights kept by 1.4.0 logic | 51,345 | 51,343 |
| …recovered from dropped segments (F9) | – | 2,743 |
| …POS-only (F3) | – | 43,034 |
| Flight dates | 07-14 → 09-15 | 07-14 → 09-30 |
| Aircraft (mapped hexes) with flights | 165 of 175 | 170 of 175, plus 200 unmapped registrations |
| City pairs | 487 | 1,022 |
| Suspect O/D (> 2.5× great circle) | 76 (0.1%) | 284 (0.3%) |
| `track_points` | 25,366,434 | 26,704,262 (ADS-B 26,145,269; POS 558,993) |
| File, build | 8.0 GB, 42 min | 8.6 GB, 31 min |

- **Matching v2 to v3.** 51,343 of the 51,345 v2 flights lie inside a v3 ADS-B flight. One now
  takes off on 07-13 (F2 extended its start) and one was the SDSC local flight (§11 item 10).
- **Endpoints changed.** 5,151 of those 51,343 flights have a different origin or destination:
  - 2,287 origins: by POS 1,336, the plan on flight number 753, the plan on tail 112, rotation 36;
  - 2,864 destinations: by the plan on flight number 2,338, rotation 269, route time on the
    extended track 125.

**Flights by sources and disposition:**

| Sources | Disposition | Flights |
|---|---|---:|
| POS | pos_only | 43,034 |
| ADSB | kept_complete | 24,659 |
| ADSB,POS | kept_rescued | 12,406 |
| ADSB | kept_rescued | 10,572 |
| ADSB,POS | kept_recovered | 2,010 |
| ADSB | kept_joined | 1,411 |
| ADSB,POS | kept_complete | 1,318 |
| ADSB,POS | kept_joined | 977 |
| ADSB | kept_recovered | 733 |

**Endpoints by source and confidence:**

| Source | Origins | Destinations |
|---|---:|---:|
| `observed` | 43,112 | 37,189 |
| `pos_observed` | 41,330 | 2,535 |
| `planned:ofp:fn` | 5,307 | 30,461 |
| `rotation` | 280 | 19,998 |
| `route_time` | 2,917 | 4,156 |
| `geometry` | 2,060 | 1,522 |
| `rotation_chained` | 1,786 | 936 |
| `planned:ofp:tail` | 328 | 299 |
| `bearing_join` | 0 | 24 |

`endpoint_confidence`: high 40,182 (41.4%), medium 39,692 (40.9%), low 17,246 (17.8%) in v3.
In **v3.1**: high 44,318 (45.6%), medium 39,692 (40.9%), low 13,110 (13.5%).
`endpoint_conflict`: 285 flights. 62,883 flights (64.7%) matched a planned leg.

**Flight numbers:** 95,153 flights (98.0%) have one, 93,422 from ACARS `FI` and 1,731 from the
plan. The learned prefix table (ACARS → plan carrier, rule-1 matches):

| ACARS prefix | Plan carrier (matches) |
|---|---|
| JJ | TAM 41,638 |
| LA | LAN 8,747; TAM 841; LPE 44 |
| LP | LPE 6,434 |
| LU | LXP 2,522 |
| 4C | ARE 428; LAN 18 |
| M3 | LTG 140 |
| L7 | LAE 111 |
| UC | LCO 94 |
| XL | LNE 89 |
| PZ | LAP 18 |

**Agreement with the plan** (flights matched on rule 1; how often the plan-free answer equals the
OFP leg):

| Plan-free source | Origins: n, agree | Destinations: n, agree |
|---|---|---|
| `observed` | 33,988, 100.0% | 29,042, 99.8% |
| `pos_observed` | 21,474, 99.5% | 1,240, 98.5% |
| `rotation` | 445, 98.4% | 24,180, 99.4% |
| `route_time` | 1,855, 77.6% | 3,500, 68.9% |
| `geometry` | 1,174, 57.8% | 527, 14.6% |
| `rotation_chained` | 313, 37.1% | 250, 51.6% |
| `bearing_join` | – | 104, 84.6% |
| `unresolved` (kept only through the plan) | 1,903 | 2,309 |

The same ADS-B flights as 1.4.0 labelled them agreed with the plan as follows:
- route time: 75.1% of origins and 72.6% of destinations;
- geometry: 67.8% and 24.9%.

Of the 4,198 inferred 1.4.0 ends the plan contradicts:
- the plan-free evidence now agrees with the plan on 2,733;
- it changes but still differs on 167;
- it leaves 1,298 unchanged.

**POS-only flights by fleet:**

| Prefix | CC | PR | PT | PS | N | HC | FA |
|---|---:|---:|---:|---:|---:|---:|---:|
| Flights | 28,194 | 8,744 | 4,391 | 1,207 | 320 | 167 | 11 |

By month: July (14th on) 8,100; August 12,665; September 22,269. Reports are a median 6.9 min
apart. Sessions dropped: 38,075 unresolved (no airport at either end and no evidence; see §11
item 4) and 4,535 invalid.

**F2 (POS ends):** 10,652 flights extended:
- 7,196 origins, +2.17 M km in total (302 km per origin);
- 3,498 destinations, +2.06 M km (589 km per destination);
- 3,746 endpoints changed.

**F9 (dropped segments):**
- `no_airport_match`: 1,801 recovered, 293 still dropped;
- `invalid`: 942 recovered, 806 still dropped.

**Validation** (`2026_0714-0930_v3_validation.txt`):
- GDAL `validate_gpkg.py` prints nothing (valid);
- the `ogrinfo` feature counts equal the rows written: flights 97,120, tracks_full 97,120,
  track_points 26,704,262, diagnostics 310,159, meta 31;
- 122 flights are split at the date line, and no part has an unsplit jump.

**KML:** `/Volumes/jy-ext/kml/kml-processed/adsb_historical_routes_output/lan_routes_2026_0714-0930_v3.kml`,
651 MB, 502,472 placemarks, 3.1 min to export (§10).
