# LAN historical routes GeoPackage, 2026-07-14 → 2026-09-30

How to build `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg` from scratch: LATAM flown
routes from ADS-B Exchange traces, with ACARS position reports (POS) filling the ADS-B
coverage holes. Every path, file and command needed to reproduce it is below.

- **Tool:** `adsb_historical_routes.py` 1.4.0 (branch `gpkg-output`), standard-library Python only.
- **Output schema:** `flown_routes` v2 (flight lines split at the date line). See
  [`docs/GEOPACKAGE.md`](docs/GEOPACKAGE.md).
- **Fleet:** the 175 ICAO hexes in `lan_active_tails_icao.csv`. All are LATAM Brasil (JJ)
  registrations (PR-, PS-, PT-), recorded under the airline code `LAN`.

---

## 1. What you need

| # | Input | Path | Notes |
|---|---|---|---|
| 1 | ADS-B traces (daily KML per aircraft) | `/Volumes/jy-ext/kml/kml-raw/lan_2026_jul-sept/` | 13,475 files `<hex>_<YYYY-MM-DD>_baro_avg.kml`, 175 hexes, **2026-07-01 → 2026-09-15**, 3.1 GB. Ignore the `runs/` subfolder (downloader logs). |
| 2 | Airports | `~/Git/kml/adsb_historical_routes/input/lan_202606-202607_airports.csv` | 118 airports, `airport,latitude,longitude,elevation_ft`. |
| 3 | Route times | `~/Git/kml/adsb_historical_routes/input/lan_202606-202607_routes_time.csv` | 481 routes, `origin,destination,avg_enroute_min`. |
| 4 | ACARS POS reports | `/Volumes/jy-ext/data_lan/trial_analysis/pos/` | One SQS JSON message per report, all LATAM fleets: 2,074,428 files (download completed 2026-10-30); 1,005,029 reports parse for this fleet. |
| 5 | Tail → ICAO hex map | `~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv` | `tail_number,icao_hex`, 175 rows. Needed to key POS reports (by registration) to the ADS-B traces (by hex). |
| 6 | Cirium schedule (only to rebuild 2 and 3) | `~/Git/kml/adsb_historical_routes/input/lan_route_cirium_202606-2020607.csv` | 1,876 rows, June and July 2026, marketing carrier LA (1,506 rows operated by JJ). The file name has a typo, `2020607`. |
| 7 | navdb (only for the QGIS project, §9.2) | `~/Git/qgis/input/navdb_2610.gpkg` | AIRAC 2610 chart layers: airports, airways, fixes, FIRs, SIDs/STARs. |
| 8 | Company routes (optional, §9) | `/Volumes/jy-ext/company_gpkg/lan/company_route_lan_2026_0507-0908.gpkg` | `company_routes` v2 from `company_route_extractor`: 9,444 variants, all LATAM carriers, OFPs 2026-05-07 → 09-08. |

Software:

- Python 3.7 or later (built and tested with 3.9.6). No packages are needed to build.
- `pytest` to run the tests (optional).
- QGIS 3.44 (`/Applications/QGIS.app`) to validate and view (optional). It provides `ogrinfo` and
  GDAL's `validate_gpkg.py`.
- About 9 GB free on `/Volumes/jy-ext` for the output (17 GB to rebuild over an existing file: the
  new one is written beside it as `.gpkg.part` until it is complete), and 8 GB of RAM.

### 1.1 Where the inputs came from

- **Traces.** Downloaded with `~/Git/kml/adsb_kml_downloader` from the 177 tails of
  `lan_active_tails_icao.csv`, `baro_avg` altitude, 2026-07-01 → 2026-09-15. The run logs are in
  the trace folder's `runs/`. To extend the range, run the downloader again for the missing dates
  into the same folder; it skips files that already exist.
- **Airports and route times.** Built from the Cirium CSV with `build_cirium_inputs.py` (§3).
  Route times use the larger of the June and July average block times.
- **POS.** LATAM's ACARS feed (`prod-rest-intake-queue.fifo`), saved one message per file as
  `<TAIL>_raw_<HHMMSS>_<uuid>.json`.

---

## 2. Get the code

```bash
cd ~/Git/kml/adsb_historical_routes
git fetch origin
git checkout gpkg-output
git pull
python3 --version                                  # 3.7+ (tested 3.9.6)
python3 -m pytest -q                               # optional: 193 passed, 1 skipped
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
| `/AN` | `PR-MYV` | Registration. It is normalized to `PRMYV` and mapped to the hex through `lan_active_tails_icao.csv`. |
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
- **Files that are skipped.** Messages from other LATAM fleets (CC-, HC-, N…) are skipped by file
  name without being opened. Other ACARS messages (OOOI and so on) are ignored.

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
    --airports   input/lan_202606-202607_airports.csv \
    --routes     input/lan_202606-202607_routes_time.csv \
    --confidence permissive --corridor-cross-track-km 50 \
    --integrate-pos true \
    --pos-folder /Volumes/jy-ext/data_lan/trial_analysis/pos \
    --tail-icao  ~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv \
    --stitch-gaps true \
    --airline LAN \
    --takeoff-from 2026-07-14 --takeoff-to 2026-09-30 \
    --format gpkg \
    --output $OUT/2026_0714-0930.gpkg \
    2>&1 | tee $OUT/2026_0714-0930_build.log
```

| Flag | Why |
|---|---|
| `--confidence permissive --corridor-cross-track-km 50` | The Route Builder's recommended preset ("custom" in the outputs), as used for VIV. It rescues flights whose ADS-B starts or ends away from the airport, which is common over Brazil's interior. |
| `--integrate-pos true` + `--pos-folder` + `--tail-icao` | Merges POS fixes into each flight's ADS-B coverage holes. This also turns on the `track_points` table, which records each point's source (`ADSB` / `POS`). |
| `--stitch-gaps true` | Spec §8.1. Stored in `meta.kml_params`, so a KML rendered later marks the remaining inferred gaps in grey. The GeoPackage geometry is the same either way. |
| `--airline LAN` | Fills `flights.airline` and `meta.airline`. |
| `--takeoff-from 2026-07-14 --takeoff-to 2026-09-30` | Keeps flights whose UTC takeoff date is in range. The traces from 07-01 to 07-12 are not read; 07-13 is read so flights airborne at midnight are completed rather than split. |
| `--format gpkg` | GeoPackage only. Make the KML later from the GeoPackage (§10). |
| `--output …/2026_0714-0930.gpkg` | A name ending in `.gpkg` names the GeoPackage itself. A fixed name is used, not `{from}`/`{to}`. |

**Runtime:** about 40 minutes (37–42 min measured) on the MacBook Pro (16 GB RAM, Python 3.9), with the data on the USB
volume. That breaks down as:

| Step | Time |
|---|---|
| Read the POS files (2.07 M; 1.0 M for this fleet) | ~7 min |
| Parse the traces, detect and join flights | ~4 min |
| Write the GeoPackage (25.4 M `track_points` rows plus indexes; 8.0 GB) | ~28–33 min |

Peak memory is about 3 GB resident. Without POS (`--integrate-pos false`, no `track_points`) the
same build takes 4.6 minutes and writes a 1.0 GB file.

---

## 7. Outputs

Everything is in `/Volumes/jy-ext/route_gpkg/lan/`:

| File | Contents |
|---|---|
| `2026_0714-0930.gpkg` | The GeoPackage: `flights`, `tracks_full`, `track_points`, `diagnostics` and `meta`. |
| `2026_0714-0930.kml.diagnostics.csv` | One row per raw segment and its outcome. The same data is in the `diagnostics` table. |
| `2026_0714-0930_build.log` | Console log of the run. The run summary is at the end. |
| `2026_0714-0930_summary.txt` | `gpkg_summary.py` output: build settings, counts, dates, sources, rescue methods, suspect O/D, flights per month, top city pairs. |
| `2026_0714-0930_validation.txt` | GDAL `validate_gpkg.py`, `ogrinfo` feature counts, rows per table, and the date-line crossing count (§8). |

Write the two text files after the build:

```bash
cd ~/Git/kml/adsb_historical_routes
OUT=/Volumes/jy-ext/route_gpkg/lan
python3 gpkg_summary.py $OUT/2026_0714-0930.gpkg -o $OUT/2026_0714-0930_summary.txt --top 25
```

and the validation file with the commands of §8.3, redirected into
`$OUT/2026_0714-0930_validation.txt`.

The CSV keeps the `.kml.diagnostics.csv` suffix even with `--format gpkg`: it is always named
after the KML stem.

---

## 8. Validate

### 8.1 The run summary

The end of `2026_0714-0930_build.log`:

```bash
tail -40 /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_build.log
```

Check these lines:

- `Takeoff-date filter 2026-07-14 .. 2026-09-30: dropped N flight(s), M remain`
- `Loaded N extra fixes across M aircraft-days`: if N is 0, the POS folder or the tail map is wrong.
- `Position-source integration: filled N real fix(es) into M of K flights`
- `Dropped N duplicate fix(es) and M implausible fix(es)`: duplicates are expected (the feed sends
  each report twice); implausible should be a small number (60 in this build).
- `Flight dates: 2026-07-14 -> …`
- `Suspect O/D (flown > 2.5x great circle): N (p%)`

### 8.2 Query the GeoPackage

```bash
G=/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg
sqlite3 $G "SELECT key, value FROM meta ORDER BY key"
sqlite3 $G "SELECT count(*), min(flight_date), max(flight_date), sum(suspect_od) FROM flights"
sqlite3 $G "SELECT sources, count(*) FROM flights GROUP BY sources"
sqlite3 $G "SELECT source, count(*) FROM track_points GROUP BY source"
sqlite3 $G "SELECT city_pair, count(*) n FROM flights GROUP BY city_pair ORDER BY n DESC LIMIT 15"
sqlite3 $G "SELECT disposition, count(*) FROM diagnostics GROUP BY disposition"
```

### 8.3 File validity with GDAL (from QGIS)

```bash
G=/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg
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

---

## 9. Open in QGIS

### 9.1 Add the layers directly (works now)

1. Start QGIS 3.44.
2. Drag `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg` onto the map, or use
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
build it yet: it has no `--project-name`, needs `--company`, and accepts flown schema v1 only.
The call then fails cleanly (exit 1, the GeoPackage untouched) and points to
`~/Git/kml/company_route_extractor/docs/handoff/qgis_prepare_partial_inputs.md`; see also
[`docs/handoff/qgis_flown_v2.md`](docs/handoff/qgis_flown_v2.md).

Once it has landed:

```bash
cd ~/Git/kml/adsb_historical_routes
python3 gpkg_to_qgz.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg \
  --navdb ~/Git/qgis/input/navdb_2610.gpkg \
  --company /Volumes/jy-ext/company_gpkg/lan/company_route_lan_2026_0507-0908.gpkg
```

This writes `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.qgz`. Alternatively, add
`--qgz --navdb ~/Git/qgis/input/navdb_2610.gpkg --company <company file>` to the §6 build.

- **One-time setup:** run `~/Git/qgis/scripts/install_plugin.sh`. Then in QGIS open
  *Plugins → Manage and Install Plugins… → Installed* and tick **Route Explorer**.
- **Open:** *File → Open Project* → `2026_0714-0930.qgz` → the Route Explorer toolbar button.
- **Checks:**

  ```bash
  cd ~/Git/qgis && source scripts/env.sh
  "$QGIS_PY" scripts/smoke_render.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.qgz
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
- **Fleet.** The flown routes cover the 175 LATAM Brasil (JJ) tails; the company file covers all
  LATAM carriers. Flights are matched by `city_pair`.
- **Moving files.** The project links its inputs by relative path. Moving or renaming any of them
  breaks it; rebuild the `.qgz` with `gpkg_to_qgz.py` (seconds, no re-parsing).
- **Keys.** The `.qgz` holds the CARTO basemap key. Don't share or commit it.

---

## 10. KML for Google Earth (optional)

Render it from the GeoPackage. Every option defaults to the build settings (`meta.kml_params`):

```bash
cd ~/Git/kml/adsb_historical_routes
python3 gpkg_to_kml.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg \
    -o /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.kml
# altitude-coloured instead:
python3 gpkg_to_kml.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg \
    -o /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_altitude.kml --color-mode altitude
```

POS stretches are orange (`ff00a5ff`), and remaining inferred gaps are grey (`ff888888`).
The default export took 2.4 minutes and 3.9 GB of memory. It writes a 325 MB KML with 111,387
placemarks for the 51,345 flights (flights with gaps are split at the 34,266 grey connectors), at
2-minute sampling: 2.24 M of the 25.4 M points.

---

## 11. Data limits for this dataset

1. **The flights end on 2026-09-15.** The traces were downloaded for 2026-07-01 → 2026-09-15
   only, so although the file is named `0714-0930`, `meta.date_to` is `2026-09-15`. To fill
   09-16 → 09-30, download those days into the trace folder (§1.1) and rebuild; the POS reports
   already cover them.
2. **Many flights start or end away from their airport.** ADS-B coverage over Brazil's interior
   is patchy:
   - 45% of flights needed an endpoint inferred (`rescue_method`: route time, geometry or bearing).
   - 6,602 flights start more than 50 km from their origin (median 396 km) and 7,960 end more
     than 50 km from their destination (median 169 km). Measured on the same build without POS;
     POS does not move flight ends (item 3).
3. **POS fills gaps inside each flight only.** POS fixes are merged between a flight's first and
   last ADS-B fix, and only into ADS-B gaps of 5 minutes or more. They never extend a flight past
   its ADS-B ends, and never create a flight:
   - POS before the first ADS-B fix exists for about 4,400 flights (on average 331 km closer to the
     origin), and after the last fix for about 4,300 (395 km closer to the destination). It is not
     used.
   - About 4,365 airborne POS sessions have no ADS-B flight within 2 hours. They are mostly a few
     aircraft whose trace files are nearly empty, and they are not in this file.
   - 10 of the 175 aircraft have no flights: E47EA0 (PR-MAG), E47EBC (PR-MAK), E47FA7 (PR-MBG),
     E480D2 (PR-MHQ), E4827E (PR-MYC), E4994D (PT-TMT), E49FBE (PS-LHA), E49FC1 (PS-LHB),
     E4A243 (PS-LHG), E4A288 (PS-LHI). Their trace files are empty or hold a few ground points
     (E4994D's largest day is 2.8 KB), yet PT-TMT, PR-MHQ and PR-MAG report POS in flight.
     **Check those hexes in `lan_active_tails_icao.csv`** against a registry: a wrong hex would
     explain empty ADS-B traces.
   - Using either would need a code change (an opt-in option). Ask before relying on them.
4. **POS needed cleaning.** The feed delivers every report twice (two ground addresses), and a
   few reports are not where the ADS-B track puts the aircraft. The merge dropped 14,068 duplicate
   fixes and 60 that would need more than 1,300 km/h to or from the ADS-B track.
5. **POS altitude is pressure altitude** (the reported flight level), about 2,000 ft below the
   ADS-B altitude at cruise in this region (§5).
6. **POS exposes some mis-joined flights.** Where POS fills a long gap it shows the real path, so
   a few joins of two flights into one now fail the O/D check. For example, several "SBBR-SBGL"
   flights of about 4½ h are flagged `suspect_od`. Exclude `suspect_od = 1` from statistics.
7. **Airports and route times come from the June–July Cirium schedule.** A check of every
   low-altitude track end found 21 more than 25 km from a listed airport, and none near another
   scheduled airport, so no airport is missing for August–September.
8. **The airline code is `LAN`,** to match the folder and file naming, although the fleet is
   LATAM Brasil (IATA JJ, ICAO TAM).
9. **No flight crosses the date line,** so every `flights`/`tracks_full` line is a single part
   (schema v2 still stores them as MULTILINESTRING).

---

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Loaded 0 extra fixes` | The wrong `--pos-folder`, a missing `--tail-icao`, or an older tool version that only reads the `<Airline>_Position=` format. Check `TOOL_VERSION` in `adsb_historical_routes.py` is 1.3.1 or later (1.4.0 for this guide). |
| `Warning: POS folder given but no tail<->hex map` | `--tail-icao` is missing or its path is wrong. |
| `Error: Cannot create output directory` | `/Volumes/jy-ext` is not mounted. |
| `Error: No KML files left inside the takeoff date range` | The trace folder has no files dated within a day of 2026-07-14 … 2026-09-30. |
| Many flights rescued to, or dropped near, one place that is not in the airports file | That airport is missing. Add it to the Cirium extract, or to `airport_overrides.csv`, and rebuild (§3). |
| The `.gpkg.part` file is left behind | The run was interrupted. Delete it and run again; the GeoPackage is only renamed into place when complete. |

---

## 13. Results of this build

Built 2026-10-30 (tool 1.4.0, `meta.created_utc` 2026-10-30T23:48:36Z):

| Measure | Value |
|---|---|
| Flights (`flights`) | **51,345**, UTC takeoff 2026-07-14 → 2026-09-15, 64 days, mean 802/day |
| Per month | July (14th on) 14,519 · August 24,814 · September (to the 15th) 12,012 |
| Aircraft with flights | 165 of 175 |
| City pairs | 487; busiest SBSP-SBRJ 1,031 and SBRJ-SBSP 1,020 |
| Raw segments | 68,418 (75.0% became flights); 3,923 dropped unjoined; 1,049 belong to the 880 flights outside the date range |
| Endpoints | 50.6% complete, 44.8% rescued (route time 30.2%, geometry 8.4%, bearing join 6.2%), 4.7% joined |
| Suspect O/D (> 2.5× great circle) | 76 (0.1%) |
| Flights with ADS-B gaps > 5 min | 12,793 (24.9%), 7,384 h of gaps |
| POS | 1,005,029 reports loaded for this fleet; **27,274** merged into **7,240** flights; 14,068 duplicates and 60 implausible fixes dropped |
| `track_points` | 25,366,434 (ADS-B 25,339,160; POS 27,274) |
| Date-line crossings | 0 |
| File | `2026_0714-0930.gpkg`, 8.0 GB; build 42 min |
| Validation | `validate_gpkg.py` clean; `ogrinfo` counts equal the rows written; all five layers load in QGIS 3.44 with no log messages |

Compared with the same build without POS: the flights, airports and dates are identical (POS
does not change detection); gaps over 5 min fall from 13,534 to 12,793 flights (8,105 h → 7,384 h);
suspect O/D rises from 71 to 76 because POS reveals the real path of a few mis-joined flights
(Data limits, item 6).
