# Handoff: reading trace JSON as well as KML (new ADS-B sources)

Written 2026-11-19 from the `adsb_kml_downloader` session. This is input for a
future change to this tool; nothing here is built yet. Everything was measured
on real files on 2026-11-18 and 2026-11-19 unless it says otherwise.

## Starting prompt

```
Read docs/handoff/2026-11-19_trace_json_sources.md. Plan support for trace
JSON inputs (ADS-B Exchange JSON, adsb.lol, and the S3 builder) alongside the
existing --kml-folder input, so each flight can use the best source. Start
with the JSON reader that reproduces today's KML results (§4–§5), using the
fixture pair in §6, before any multi-source selection (§8). Brainstorm the
approach with me first.
```

## 1. Why

- **This tool reads one input type today.** `--kml-folder` takes the KML that
  `~/Git/kml/adsb_kml_downloader` saved from the ADS-B Exchange website
  ("Export KML").
- **The website is no longer a reliable source.** Since November 2026 it asks
  for a human check on most page loads.
- **New sources are on the way.** Each is a daily trace file per tail, mostly
  in JSON:

| Source | Project | State |
|---|---|---|
| ADS-B Exchange website, JSON | `adsb_kml_downloader` v1.4 (branch `turnstile-gate`); JSON is now the default (`--format json`) | Files exist, e.g. 3,851 AMX traces |
| ADS-B Exchange website, KML | `adsb_kml_downloader --format kml` (legacy) | What this tool reads today |
| adsb.lol daily archives | `~/Git/kml/adsb_trace_lol` | Design only (handoff written 2026-11-18) |
| S3 feeds `turbofire_adsb`, `turbofire_firehose` | `~/Git/kml/adsb_trace_builder` | Phases 1–2 done, writing output in Phase 3 |

- **Keep each input as close to its source as possible.** The source
  projects only extract; this tool decides per flight which source, or which
  mix, gives the best output.

## 2. Files and folders by source

**Folders:**
- Put one source in each folder. Never mix sources in one folder.
- ADS-B Exchange JSON and adsb.lol use the **same file name**, so only the
  folder tells them apart.

| Source | Folder (example) | Trace file | No-data | Other |
|---|---|---|---|---|
| ADS-B Exchange JSON | `amx_traces_2026-0301-0531/` (any name the user gives `-o`) | `<icao>_<YYYY-MM-DD>_trace.json`, plain JSON | `<icao>_<YYYY-MM-DD>_nodata.json` | `runs/` logs |
| ADS-B Exchange KML | same kind of folder | `<icao>_<YYYY-MM-DD>_<baro_avg\|geometric\|pressure>.kml` | none | |
| adsb.lol | `<airline>_<start>_<end>_traces_lol/`, e.g. `amx_2026-03-01_2026-05-30_traces_lol/` | `<icao>_<YYYY-MM-DD>_trace.json`, plain JSON | `manifest.csv` rows (marker files undecided) | `manifest.csv`, `SOURCE.md`, `LICENSE-ODbL.txt` |
| S3 builder (planned) | `<output>/<feed code>/` | `<icao>_<YYYY-MM-DD>_<code>_trace.json` and/or `<icao>_<YYYY-MM-DD>_<code>_baro.kml` | `<icao>_<YYYY-MM-DD>_nodata.json` | `report/` |

**Naming:**
- `<icao>` is the 6-digit ICAO hex in lower case.
- `extract_aircraft_id()` already takes the text before the first `_` and
  upper-cases it, so every source keys aircraft the same way.
- The date is the UTC day the file covers. A flight across midnight is split
  between two files, as with KML today.

**Suggested discovery patterns** (fixed names only; skip anything else with a
warning):

```
^(?P<icao>[0-9a-f]{6})_(?P<date>\d{4}-\d{2}-\d{2})_(?:(?P<code>[a-z0-9]+)_)?trace\.json$
^(?P<icao>[0-9a-f]{6})_(?P<date>\d{4}-\d{2}-\d{2})_nodata\.json$
^(?P<icao>[0-9a-f]{6})_(?P<date>\d{4}-\d{2}-\d{2})_(?P<alt>baro_avg|geometric|pressure|[a-z0-9]+_baro)\.kml$
```

**Manual exports in a download folder.** The AMX folder holds two hand-made
website exports, `N105JS-track-EGM96_avg.kml` and `N105JS-track-EGM96_avg (1).kml`.
- Today's `--kml-folder` globs `*.kml`, so it would read them as aircraft
  `UNKNOWN`.
- The patterns above skip them.

**No-data marker** written by the downloader:

```json
{"icao": "0d0380", "date": "2026-04-29", "http_status": 404, "source": "globe.adsbexchange.com", "checked_utc": "2026-11-19T17:04:14Z"}
```

It means "checked, no data". A missing file means "not downloaded". Tell the
two apart in diagnostics.

## 3. The JSON format (readsb `trace_full`)

ADS-B Exchange JSON and adsb.lol are the same format, because both run readsb.
The S3 builder writes the same layout with some fields empty (§3.4).

### 3.1 Top level

| Key | Meaning | ADS-B Exchange | adsb.lol |
|---|---|---|---|
| `icao` | hex, lower case | yes | yes |
| `r` | registration with hyphen, e.g. `XA-AAC`, `N105JS` | yes | yes |
| `t`, `desc` | type code, description | yes (`desc` sometimes absent) | yes |
| `dbFlags` | database flags | yes | yes |
| `timestamp` | epoch seconds of 00:00 UTC of the day; point times are offsets from it | yes | yes |
| `ownOp`, `year` | operator, build year | usually | no |
| `version` | readsb version | no | yes |
| `trace` | list of points | yes | yes |

S3 builder files add a `builder` key (source, feed, version, counts).

### 3.2 Point array (14 values, confirmed on 81,301 ADS-B Exchange and 58,005 adsb.lol points)

| Index | Field | Notes |
|---|---|---|
| 0 | seconds after `timestamp` | float, ms precision |
| 1 | latitude | |
| 2 | longitude | |
| 3 | altitude, ft | barometric (pressure altitude); the string `"ground"` on the ground; `null` if unknown. If flag 8 is set, this value is geometric. |
| 4 | ground speed, kt | or `null` |
| 5 | track, deg | true heading when on the ground |
| 6 | flags | bit 1 stale (gap before this point), bit 2 start of a new leg, bit 4 vertical rate is geometric, bit 8 index 3 is geometric altitude |
| 7 | vertical rate, ft/min | |
| 8 | details object or `null` | on about 25% of points: `flight` (callsign), `squawk`, `alt_geom`, `nav_qnh`, `nav_altitude_mcp`, `nic`, `nac_p`, `category`, … |
| 9 | position source | `adsb_icao`, `mlat`, `tisb_icao`, `adsc` (satellite), `other`; S3 files put the feed code here |
| 10 | geometric altitude, ft | WGS84 ellipsoid height; present on about 90% of points |
| 11 | geometric vertical rate | |
| 12 | indicated airspeed | |
| 13 | roll | |

The website's own reader (`updateTraceData` in the site JavaScript) uses these
same indices.

### 3.3 Example (ADS-B Exchange, `0d0380`, 2026-03-15)

```
[12.75, 20.671555, -99.897308, 33125, 384.1, 316.6, 0, 1088, null, "adsb_icao", 34825, null, null, null]
[18851.72, 25.774124, -100.098871, "ground", 47.5, 120.9, 0, 0, {"flight": "SLI998  ", "alt_geom": 1200, "nav_qnh": 1009.6, ...}, "adsb_icao", 1200, null, null, null]
```

### 3.4 S3 builder differences (from its design §5.4; check its README once released)

- `flags` is always 0.
- Index 9 is the feed code.
- Fields the feed lacks are `null`.
- Index 8 holds the feed record's other fields (`flight`, `orig`, `dest`,
  `flight_id`, …). This is the same metadata `load_firehose_points()` reads
  today.
- `timestamp` is the first point's epoch, not midnight.
- Altitude is barometric feet only.

## 4. How the website KML is made from the same trace

This comes from the site's `exportKML` / `coordsForExport` code, saved
2026-11-18, and was checked against real files in §6.

**Structure:** `kml › Folder › Folder("<REG> track") › Placemark…`.
- Each Placemark has `name` = registration in upper case (or icao), and one
  `gx:Track` of `when` and `gx:coord` elements: `lon lat alt_m`, time in UTC
  with ms and `Z`.
- Style: `LineStyle` colour `ff1c1ae3`, width 4.

**Sections:**
- The day is cut into **alternating airborne and ground Placemarks** where
  index 3 switches between a number and `"ground"`.
- Ground sections have altitude 0 and no `altitudeMode` (clamped).
- Airborne sections have `altitudeMode absolute`.
- The last point of each section is written twice.
- The AMX example `N105JS` on 2026-03-01 has 10 Placemarks: 5 ground and 5
  airborne.

**What `parse_kml_tracks()` makes of this today:** each Placemark becomes one
track. Ground Placemarks are then dropped by `track_has_useful_altitude()`
(90% zeros). So **today's input is effectively the airborne sections only**,
and `combine_segments_intelligently()` joins them per aircraft.

**Altitude by export button** (downloader `-a`; the site file name suffix in
brackets):

| `-a` | Site style | Altitude written (metres, rounded) |
|---|---|---|
| `baro_avg` (default) | `geom_avg` (`EGM96_avg`) | barometric + a running average of (geometric − barometric), converted to height above the EGM96 geoid. The average uses an 8 s window, runs forwards then backwards, and resets when the step exceeds 50 ft. With no average yet it falls back to geometric. |
| `geometric` | `geom` (`EGM96`) | geometric (index 8's `alt_geom` when present, else index 10), converted to EGM96 |
| `pressure` | `baro` (`press_alt_uncorrected`) | barometric × 0.3048, no correction |

**Points the KML leaves out:**
- points with no position;
- points with no altitude;
- with `baro_avg` or `geometric`: points that have no geometric reference.

## 5. Recommended JSON reader

Write `parse_trace_json(path, aircraft_id)`, returning **the same tuples as
`parse_kml_tracks()`** so the rest of the pipeline is unchanged.

1. **Load the file.** Load the JSON; gunzip first if the file starts with
   `1f 8b`, to be robust.
2. **Registration:** `r` upper-cased, else `aircraft_id`.
3. **Each point:**
   - Skip it if index 1 or 2 is `null`, or out of range.
   - Time: `timestamp + p[0]`, formatted
     `YYYY-MM-DDTHH:MM:SS.mmmZ`.
4. **Altitude in metres** (`TrackPoint.alt` is metres):
   - **Recommended, simple:** use `p[8]["alt_geom"]` if present, else `p[10]`.
     If both are null, use `p[3]`. Multiply by 0.3048. `"ground"` gives 0;
     a `null` altitude skips the point.
     - On the fixture this differs from the default KML by a median of 2 m
       (range −29 to +39 m), because it skips the EGM96 geoid step.
     - Geoid separation can reach about ±100 m elsewhere in the world. That
       is small next to airport and route detection thresholds, so check it
       in the tests.
   - **Exact match (optional):** reproduce the `baro_avg` running average and
     convert with an EGM96 grid, e.g. `pyproj` with PROJ's
     `us_nga_egm96_15.tif`, or `pygeodesy`. Only worth it if results must be
     identical to the KML runs.
   - **Do not use `p[3]` alone for airborne points.** On the fixture the
     default KML is a median **597 m higher** than barometric (range 63 to
     757 m), because geometric and barometric altitude differ in warm air.
     Detection tuned on KML would shift.
5. **Sections:** split into sections where the point switches between ground
   and airborne, as the KML does. Ground sections then drop out through
   `track_has_useful_altitude()`, exactly as now.
   - Alternative: keep one track and let detection use the ground points.
     That's a behaviour change; decide it separately.
6. **Keep the extra fields** on the TrackPoint:
   - `groundspeed` from index 4; today it is computed from positions;
   - vertical rate from index 7;
   - the callsign from `p[8]["flight"]` (strip it), the same evidence the
     FlightAware identity uses;
   - the position source from index 9;
   - the stale and leg flags from index 6.
   - Set `TrackPoint.source` to a label per source, e.g. `ADSBX`, `ADSBLOL`,
     `S3_ADSB`, `S3_FIREHOSE`.
   - These cost nothing to keep and give the per-flight choice its evidence.
7. **Style:** the KML values (`ff1c1ae3`, width 4, opacity `ff`), or a colour
   per source.

**When both JSON and KML exist** for one tail-day and source, read the JSON
and skip the KML with a note. The JSON holds every point the KML has, and
more.

## 6. Proof and test fixture

**Fixture pair** (copy into `tests/fixtures/`; the two files are under 1 MB
together):
- `/Volumes/jy-data/kml/kml-raw/amx_traces_2026-0301-0531/a01820_2026-03-01_trace.json`
  (ADS-B Exchange JSON);
- `/Volumes/jy-data/kml/kml-raw/amx_traces_2026-0301-0531/N105JS-track-EGM96_avg.kml`:
  the same aircraft-day exported by hand from the website, `baro_avg` style.

**Measured on that pair, 2026-11-19:**

| Check | Result |
|---|---|
| KML points / unique times | 2,677 / 2,650 (27 section-end duplicates) |
| JSON points | 2,761 |
| KML times missing from JSON | 0 |
| Position difference on matching times | 0.0 (identical) |
| JSON points not in KML | 94 = 81 with no altitude + 13 with no geometric reference |
| Ground Placemark points | all `"ground"` in JSON |
| KML altitude − JSON geometric × 0.3048 | median 2 m, range −29..39 m |
| KML altitude − JSON barometric × 0.3048 | median 597 m, range 63..757 m |

**Suggested test:** the JSON reader on the fixture gives:
- the same airborne track count as `parse_kml_tracks()` on the KML;
- the same point times, minus the 27 duplicates;
- altitudes within 40 m;
- the same flights detected end to end.

## 7. What is known about the sources' quality (for the per-flight choice)

These are facts measured so far, not rules.

- **adsb.lol against ADS-B Exchange** (AMX, 2 days, 15 aircraft-days, 61
  flights):
  - adsb.lol covers 32–76% of ADS-B Exchange's airborne time.
  - Every adsb.lol point was also in ADS-B Exchange, so it is a subset.
  - 16 flights were at least 90% covered; 11 were under 10%.
  - Gaps run up to 26 minutes.
  - It sees about 83–85% of the fleet on a given day.
- **S3 feeds** (the trace builder's research):
  - Where both feeds have points they agree to about 0.2 nm, with a steady
    1–2 s clock offset.
  - The `adsb` feed had runs of bad positions (about 285 nm off) and 10–30 s
    timestamp jitter on some days.
  - The firehose covered only about 15 AMX tails a day in March 2026.
  - Coverage changes by airline, region and day, so never rank feeds by name.
- **Point rate:** the S3 feeds give about one point a minute (a known
  limitation the user accepts). ADS-B Exchange and adsb.lol give several a
  minute, unevenly.
- **Missing days:** ADS-B Exchange no-data days had no adsb.lol data either.

## 8. Ideas for using several sources (for the brainstorm, not decisions)

1. **Smallest step.** Keep the current pipeline:
   - The primary track for a tail-day is the best single file; extra sources
     fill its gaps through `merge_track_with_sources()`. Its dedup, gap-fill
     and speed checks already fit, since it does this for POS and firehose
     today.
   - Promote the next source to primary when the first is missing or no-data.
   - Choosing primary by measured coverage per tail-day, not by a fixed
     order, follows the user's rule for the S3 feeds.
2. **Per flight.** Detect flights per source separately, match them by time
   overlap, and score each per flight:
   - share of flight minutes with a point within 2 min;
   - longest gap;
   - whether the takeoff and landing are seen.
   - Then pick the best, or fill its gaps from the others.
   - Keep `source` per point so the output colours and diagnostics show where
     each stretch came from, as POS and firehose do now.
3. **Never interleave** two dense sources point by point. Clock offsets and
   jitter make false zig-zags; fill gaps instead.
4. **Keep the inputs apart.** Inputs stay one folder per source, e.g. a
   repeatable `--trace-folder LABEL=PATH`, or one flag per source. A trace
   file is never edited.

## 9. References

- Downloader, file formats and markers: `~/Git/kml/adsb_kml_downloader/README.md`
  (branch `turnstile-gate`).
- adsb.lol project:
  - `~/Git/kml/adsb_trace_lol/docs/HANDOFF.md`;
  - the design `docs/superpowers/specs/2026-11-18-adsb-trace-lol-design.md`;
  - the research `docs/research-2026-11-18.md`.
- S3 builder:
  - `~/Git/kml/adsb_trace_builder/docs/superpowers/specs/2026-11-18-adsb-trace-builder-design.md`
    §5.4;
  - `RESEARCH.md`.
- **Correction to the S3 builder design.** Its §5.4 says the website KML
  "repeats each leg as a ground-clamped copy". The site code and the fixture
  show alternating ground and airborne sections, with no repeated legs. Its
  single absolute track per day still loads here, but a section split like
  §5.5 would behave more like the website KML.
