# Request: endpoint evidence, ACARS identity and POS-first flights (`flown_routes` v3)

**From:** `direct_detect` (`~/Git/analytics_tools/direct_detect`), 2026-11-03, after an audit of the
LAN GeoPackage `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg` (tool 1.4.0). The audit is in
`direct_detect/docs/DEVLOG.md` (2026-11-03) and `docs/project_assumptions.md` A-72.

**Asked by the owner:** the route builder should use every input that is available, each optional,
to give the highest route match rate for any airline. Cirium stays as a fallback because most
airlines will not have OFPs. When this is done, rebuild the LAN GeoPackage and a KML of it.

---

## 1. Why: what the audit measured

Run scope 2026-07-14 → 2026-09-08. There are 100,912 auth OFPs and 45,608 tracks (the 175 LATAM
Brasil hexes). direct_detect matches each track to an OFP by tail, time and origin/destination;
the GeoPackage has no flight number (`callsign` is empty on all 51,345 rows).

| Finding | Number |
|---|---:|
| OFPs matched to a track | 38,589 (38%); TAM (LATAM Brasil) 77% |
| OFPs on tails the ADS-B download does not cover (CC- 46,457, HC- 1,529, N- cargo about 1,960, 677 PR/PS tails missing from the tail map) | 50,990 |
| Flights with a builder-inferred endpoint (`kept_rescued`) | 49% |
| Tracks whose O/D contradicts the OFP the tail had at that time | 6,040 |
| ...of which exactly one unused OFP on the same tail and time shares the observed end | 3,266 |
| Covered-tail OFPs with no track; only the aircraft's next leg exists (leg missing from the KML) | 4,870 |
| Covered-tail OFPs where only a dropped segment exists (`invalid` 394, `no_airport_match` 363) | 757 |

**The endpoint guesses fail between airports on one line.** Route time, bearing and corridor
cannot separate them when ADS-B is lost en route; the loose end's median altitude is FL309. The
most frequent swaps (OFP → builder):

| OFP pair | Builder pair | Tracks |
|---|---|---:|
| SBGR-SBMO | SBGR-SBAR | 160 |
| SBGR-SBFI | SBGR-SBCA | 127 |
| SBGR-SBBE | SBGR-SBPJ | 109 |
| SBGR-SBUL | SBGR-SBUR | 106 |
| SBGR-SBAR | SBGR-SBSV | 96 |
| SBGR-SBMG | SBGR-SBLO | 90 |
| SBVC-SBGR | SBSG-SBGR | 86 |
| SBMO-SBGR | SBRF-SBGR | 77 |

**The aircraft's own sequence of flights is a strong signal that needs no OFP.** For those 3,266
tracks, where the neighbouring flight's end was *observed* (the next takeoff for a guessed
destination, the previous landing for a guessed origin):

| Neighbour airport agrees with | Tracks |
|---|---:|
| the OFP | 496 (69%) |
| the current builder guess | 85 (12%) |
| neither (a leg probably missing from the data) | 141 (19%) |

In most other cases the neighbour's end was guessed too, so errors chain from one segment to
the next.

**POS is mostly unused.** The 1.4.0 build loaded 1,005,029 POS fixes for this fleet and merged
27,274. The reasons are in `historical_route_geopackage_instructions_lan.md` §11 item 3:
- POS fills only gaps between a flight's first and last ADS-B fix;
- it never extends a flight's ends (POS exists before about 4,400 flights and after about 4,300);
- it never creates a flight (about 4,365 airborne POS sessions have no ADS-B flight);
- messages from unmapped fleets are skipped.

The folder is 2,074,428 files, **all LATAM fleets**. A 1,500-message sample was 100% `POS`
reports, 50% from CC- aircraft. **Every report names its flight**, for example:

```
FI JJ8126/AN CC-BMD
-  POSN16022W065082,RAF16,171807,380,ETE17,172225,N16270W065376,M53,19932,498/TS171807,050926630D
```

**The Cirium inputs cover the network.** 99% of OFP pairs are in
`lan_202606-202607_routes_time.csv`. Airports in OFPs but missing from `_airports.csv`:

| Airport | OFP endpoints |
|---|---:|
| SGAS | 230 |
| SDSC | 49 |
| SCNT | 22 |
| LFBO, SBSJ, SPZO | 1 each |

---

## 2. Principles, goals and non-goals

**Principles (owner, 2026-11-03)**
- **The unit is the 24-hour ADS-B stream of one aircraft (ICAO hex).** Cut it into as many flights
  as possible between known airports, using:
  - airport latitude, longitude and elevation;
  - the airport radius and the takeoff / arrival heights;
  - the route times.

  This is today's segmentation; keep it the core.
- **Then fill the coverage gaps with POS, by tail and time.** The gaps are:
  - holes inside a flight (as 1.4.0);
  - the stretch before the first or after the last ADS-B fix, where coverage was lost;
  - whole legs with no ADS-B at all.

  The tail → hex map gives the aircraft, and the time places the fix.
- **No flight is ever required to have a flight number or a planned leg.** The ACARS flight number
  (F1) and planned legs (F5) are optional evidence. They may confirm an endpoint, fill one, or
  flag a conflict. Their absence never drops, splits or downgrades a flight.
- **Keep as many flights as possible.** A segment that 1.4.0 drops (`invalid`,
  `no_airport_match`) should be completed with the new evidence where it can be (F9). Report what
  is still dropped and why.

**Goals**
1. Every new input and behaviour is **optional and off by default**. With all of them off, the
   output equals 1.4.0. This is proven by the existing tests plus one equivalence test.
2. Each endpoint (origin and destination) carries **where it came from**, and the candidates the
   builder considered, so a consumer can decide what to trust.
3. Use the evidence that exists for the airline, in a fixed order (§4). It must work with ADS-B
   only, ADS-B + Cirium, ADS-B + POS, or with planned legs (OFP or schedule) as well.

**Non-goals**
- No change to how ADS-B segments are detected.
- No new download. The KML traces and POS files are what they are; see §8 for gaps.
- No direct_detect logic in this tool. direct_detect keeps its own exact-flight join rules and
  reads the provenance.

---

## 3. Inputs

| # | Input | Path (LAN) | New? | Used for |
|---|---|---|---|---|
| 1 | ADS-B KML traces | `/Volumes/jy-ext/kml/kml-raw/lan_2026_jul-sept/` (175 hexes, 07-01 → 09-15) | no | tracks |
| 2 | Airports | `input/lan_202606-202607_airports.csv` plus `airport_overrides.csv` | add 6 airports | endpoints |
| 3 | Route times | `input/lan_202606-202607_routes_time.csv` | no | fallback rescue |
| 4 | ACARS POS | `/Volumes/jy-ext/data_lan/trial_analysis/pos/` (2,074,428 files, all fleets) | read all fleets | fixes, flight number, POS-only flights |
| 5 | Tail → hex | `~/Git/kml/adsb_kml_downloader/input/lan_active_tails_icao.csv` | no | POS ↔ ADS-B join |
| 6 | **Planned legs** | `/Volumes/jy-ext/data_lan/trial_analysis/planned_legs/lan_ofp_legs_2026-07-01_2026-09-08.csv` | **yes, optional** | identity and endpoints |

**Planned legs CSV** (written by direct_detect from the auth OFPs, latest plan per flight;
124,390 rows; **OFPs end 2026-09-08**):

```
leg_key,source,carrier_icao,flight_number,flight_digits,tail,orig,dest,sched_dep_utc,ofp_date,ambiguous
2026-07-14_TAM3036_SBSP-SBCF_b2,ofp,TAM,TAM3036,3036,PRMAG,SBSP,SBCF,2026-07-14T11:00:00Z,2026-07-14,0
```

- `tail` is canonical: upper case, no hyphen.
- `source` names the provider (`ofp`, later `cirium_flight`, `timetable`, ...).
- `ambiguous` = 1: the plan's tail or identity was uncertain; never use such a row alone.
- The flag accepts several files, so a flight-level Cirium schedule converted to these columns
  needs no further code.

**Flight-number prefixes.** OFP flight numbers use ICAO prefixes: TAM 59,865, LPE 22,381,
LAN 14,971, ARE 13,911, LNE 4,589, LXP 4,357, LAE 1,648, LCO 1,276, LTG 883, LAP 501. ACARS
`FI` uses IATA prefixes (`JJ8126` on CC-BMD).
- Match legs on **`flight_digits`**, together with the tail and time, so the prefix never matters.
- Learn the IATA ↔ ICAO prefix table from the matches, and report it in the build log and in
  `meta`. Do not hard-code it.

---

## 4. Features

Each feature has its own flag; each needs synthetic tests.

### F1 ACARS flight identity (`--acars-identity true`)
- Parse `FI <flight>/AN <reg>` from every POS report. The parser already finds `/AN`.
- Give each flight the flight number of the POS reports inside its time window (same aircraft).
  New columns: `flight_number` and `flight_number_source = acars_fi`.
- More than one distinct number inside one flight is written to `flight_number_conflict = 1`.
  It is a mis-join signal, so keep the majority value and say so in the diagnostics.
- Fill the existing `callsign` column only if the KML gives one; it does not today.

### F2 POS before and after the ADS-B ends (`--pos-extend-ends true`)
- Add POS fixes for the same aircraft (tail → hex) before the first and after the last ADS-B fix,
  while all of these hold:
  - each step passes the existing `MAX_FIX_SPEED_KMH` check;
  - the time gap between consecutive fixes is ≤ `--pos-extend-max-gap-min` (default 60);
  - the extension stops at a ground stop or at the neighbouring flight's window.
- When an `FI` flight number is known, a change of number is a further stop signal. It is never
  required.
- Then re-run endpoint detection on the extended track. An extended end within the airport radius
  and below the arrival / takeoff height counts as **observed** (`*_source = pos_observed`).
- Diagnostics: count of flights extended, km gained at each end, endpoints that changed.

### F3 POS-only flights (`--pos-only-flights true`, `--pos-fleets mapped|all`)
- Group airborne POS sessions that no ADS-B flight absorbs by registration and time continuity.
  Split a session at a ground stop, or at a time gap longer than the route time allows. A change
  of `FI` flight number is an extra split hint when present; it is never required. Build flights
  from the sessions:
  - `sources = 'POS'`, `disposition = 'pos_only'`;
  - endpoints come from the evidence order (§4.8).
- `--pos-fleets all` builds them for registrations not in the tail map as well (CC-, HC-, N-).
  Those rows have `registration` set and `aircraft_id` NULL; say so in `docs/GEOPACKAGE.md`.
- They are sparse (minutes between fixes). Write `n_points_full` honestly; never interpolate.
- Report: POS-only flights by registration prefix and by month, and the median spacing between
  fixes.

### F4 Aircraft rotation (`--rotation-endpoints true`, `--rotation-max-gap-h 18`)
- Per aircraft (hex, else registration), order the flights by time:
  - a destination that is not observed takes the next flight's origin;
  - an origin that is not observed takes the previous flight's destination;
  - only when that neighbour end is observed (ADS-B or POS), and the ground gap is between 0 and
    `--rotation-max-gap-h`.
- Iterate until nothing changes. A neighbour end that is itself inferred may be used only in
  that fixed-point pass, and the end is then marked `rotation_chained` (lower confidence).
- If rotation disagrees with an observed end of the same flight, keep the observed end and set
  `endpoint_conflict = 1`.
- Use the airport radius already in force.

### F5 Planned legs (`--planned-legs <csv> [<csv> ...]`)
- Match a flight to one leg, strictest rule first:
  1. `flight_digits` = the flight's number (F1), and the same tail, and the takeoff within
     `[sched_dep − 60 min, sched_dep + 8 h]`. Same window as direct_detect.
  2. Without a flight number: the same tail, the takeoff in that window, **and** one observed end
     equal to the leg's.
- Use it only when it is unique: one leg for the flight, and that leg claimed by no other flight.
- Rows with `ambiguous = 1` never match on their own.
- A matched leg:
  - fills endpoints that are not observed (`*_source = planned:<source>:fn` or `planned:<source>:tail`);
  - fills `flight_number` when F1 found none (`flight_number_source = planned:<source>`);
  - writes `planned_leg_key`;
  - **never overrides an observed end.** A disagreement with an observed end sets
    `endpoint_conflict = 1` (a probable tail swap or diversion).
- **Before** planned legs are applied, write the endpoint the builder would have given without
  them into `orig_free` / `dest_free`. This lets one run measure how accurate the planned-leg-free
  evidence is, and keeps a consumer from counting a planned-leg endpoint as independent agreement
  with the same plan.

### F6 Candidates and confidence (always on when any rescue runs; additive columns)
- Record every candidate airport that passed the route-time / geometry tests:
  `orig_candidates` / `dest_candidates`, a `|`-separated list in rank order.
- The choice stays as in 1.4.0.
- `endpoint_confidence`:
  - `high`: observed, or a planned leg on rule 1;
  - `medium`: POS-observed, rotation from an observed neighbour, or a planned leg on rule 2;
  - `low`: route time, geometry, bearing join or `rotation_chained`, plus any end with more than
    one candidate.

### F7 Airports
- Add SGAS, SDSC, SCNT, LFBO, SBSJ and SPZO, through `airport_overrides.csv` or a Cirium rebuild.
- When `--planned-legs` is given, warn about every leg airport missing from the airport list, and
  list them in the build log.

### F9 Recover dropped segments (`--recover-dropped true`)
- 1.4.0 dropped 3,923 LAN segments: 2,144 `no_airport_match`, 1,779 `invalid`. 757 OFPs on
  covered tails have only such a segment near their departure time.
- Before dropping a segment, try F2 (POS ends), F4 (rotation), the airports added by F7, and F5
  when given. Keep it if it then has two endpoints by the evidence order. Otherwise drop it as
  before, with the reason.
- Report the recovered and still-dropped counts per reason.

### F8 Evidence order per endpoint
Highest first. The first one that applies wins; every one that applied goes into diagnostics.

1. Observed in ADS-B (as 1.4.0).
2. Observed in POS after F2 (`pos_observed`).
3. Planned leg, rule 1 (flight number, tail, time).
4. Rotation from an observed neighbour.
5. Planned leg, rule 2 (tail, time, one observed end).
6. Route time, geometry, bearing join (as 1.4.0), then `rotation_chained`.

The old `disposition` and `rescue_method` keep their 1.4.0 meaning: `rescue_method` names the
1.4.0 rescue if one ran. The new `*_source` columns give the final answer.

---

## 5. Output: `flown_routes` v3 (additive)

- `meta.schema_version = "3"`. Every v2 table, column and meaning stays.
- New `flights` columns:
  - `flight_number`, `flight_number_source`, `flight_number_conflict`;
  - `orig_source`, `dest_source`: `observed`, `pos_observed`, `planned:<src>:fn`,
    `planned:<src>:tail`, `rotation`, `rotation_chained`, `route_time`, `geometry`,
    `bearing_join`, `unresolved`;
  - `orig_free`, `dest_free`, `orig_candidates`, `dest_candidates`;
  - `endpoint_confidence`, `endpoint_conflict`, `planned_leg_key`, `n_pos_fixes`.
- `disposition` gains `pos_only`; `aircraft_id` may be NULL when `registration` is set (POS-only,
  unmapped fleet).
- `meta`:
  - every new flag and value, the planned-legs file names with row counts and date ranges;
  - the learned prefix table;
  - the per-source endpoint counts.
- `diagnostics`: one row per change F2, F4 or F5 made, with the before and after airports.
- Update `docs/GEOPACKAGE.md`. Write `docs/handoff/direct_detect_flown_v3.md` for direct_detect:
  the new columns, the meaning of each `*_source`, and how `*_free` should be used.

---

## 6. Acceptance

1. **Equivalence:** with every new flag off, `pytest` passes as before (193 passed, 1 skipped at
   1.4.0), plus a test that the v3 writer with the flags off gives the same `flights` rows and
   geometry as 1.4.0 on a fixture.
2. **Unit tests** per feature on synthetic data:
   - F1: number found / conflict;
   - F2: extend to an observed end / stop at a speed jump / stop at a gap;
   - F3: a session becomes a flight / an unmapped fleet only with `all`;
   - F4: observed neighbour / chained / conflict / gap too long;
   - F5: rule 1 / rule 2 / ambiguous row / two flights claiming one leg / no override of an
     observed end;
   - F6: confidence levels;
   - F8: order;
   - F9: a dropped segment recovered / still dropped;
   - **a flight with no flight number and no planned leg is kept exactly as it would be without F1 / F5.**
3. **LAN report.** Put it in the build summary and in a short `..._evidence_report.md` next to the
   GeoPackage:
   - flights by `sources` and `disposition`;
   - endpoints by `*_source` and `endpoint_confidence`;
   - share with a flight number;
   - POS-only flights by registration prefix;
   - flights extended by F2 and the km gained;
   - dropped segments recovered by F9, per reason;
   - **agreement table:** for flights matched to a planned leg on rule 1, how often `orig_free` /
     `dest_free` equal the leg, split by the evidence that produced them. This is the accuracy of
     the OFP-free logic.
4. **Expected direction (sanity, not targets):**
   - most of the about 7,000 contradicted endpoints change;
   - rotation agrees with the plan clearly more often than route time;
   - flight number coverage is high on ADS-B flights that have POS in their window;
   - CC- fleets appear as POS-only flights.
   If not, stop and report before the full build.

---

## 7. LAN rebuild

**Do not overwrite** the v2 file. It is direct_detect's baseline.

```bash
cd ~/Git/kml/adsb_historical_routes
OUT=/Volumes/jy-ext/route_gpkg/lan
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
    --stitch-gaps true --airline LAN \
    --takeoff-from 2026-07-14 --takeoff-to 2026-09-30 \
    --format gpkg \
    --output $OUT/2026_0714-0930_v3.gpkg \
    2>&1 | tee $OUT/2026_0714-0930_v3_build.log
python3 gpkg_summary.py $OUT/2026_0714-0930_v3.gpkg -o $OUT/2026_0714-0930_v3_summary.txt --top 25
# validation as in the LAN guide §8.3, into $OUT/2026_0714-0930_v3_validation.txt
python3 gpkg_to_kml.py $OUT/2026_0714-0930_v3.gpkg \
    -o /Volumes/jy-ext/kml/kml-processed/adsb_historical_routes_output/lan_routes_2026_0714-0930_v3.kml
```

- Flag names are suggestions; keep the ones you implement and update this block.
  **Implemented (1.5.0) exactly as above**, plus `--pos-extend-max-gap-min` (60) and
  `--rotation-max-gap-h` (18). `--airports` now takes several files; the second one adds the six
  F7 airports (see `historical_route_geopackage_instructions_lan_v3.md` §1).
- The 1.4.0 POS read skips unmapped fleets by file name. With `--pos-fleets all` it opens all
  2.07 M files. Read the folder with `os.scandir`, never `ls`, and say how long it took.
- Disk: 193 GB free on `/Volumes/jy-ext` (2026-11-03). The v2 file was 8.0 GB, and v3 will be
  larger with POS-only flights.
- Write `historical_route_geopackage_instructions_lan_v3.md`, a copy of the LAN guide brought up
  to date (inputs, flags, results, data limits). Keep the 1.4.0 guide as it is.

---

## 8. Known limits (state them in the v3 guide)

1. **ADS-B ends 2026-09-15.** POS runs later, so 09-16 → 09-30 will be POS-only flights unless the
   traces are downloaded (`~/Git/kml/adsb_kml_downloader`, a separate project; owner's call).
2. **Planned legs end 2026-09-08,** where the OFPs end.
3. **No ADS-B for CC-, HC- and N- fleets.** The downloader has no hexes for them. POS-only flights
   are the only route coverage there until hexes are added.
4. **Ten mapped hexes have no flights** (E47EA0 PR-MAG, E47EBC PR-MAK, E47FA7 PR-MBG, E480D2 PR-MHQ,
   E4827E PR-MYC, E4994D PT-TMT, E49FBE PS-LHA, E49FC1 PS-LHB, E4A243 PS-LHG, E4A288 PS-LHI).
   - Check them against a registry.
   - F3 with `mapped` will give them POS-only flights if their POS exists.
5. **The Cirium inputs are June–July.** They are route-level averages, not dated flight-level
   schedules. A dated flight-level extract converted to the planned-legs columns would let
   airlines without OFPs use F5.
