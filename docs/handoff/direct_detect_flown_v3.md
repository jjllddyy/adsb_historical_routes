# Handoff to `direct_detect`: `flown_routes` v3 (endpoint evidence)

**From:** `adsb_historical_routes`, 2026-11-03 (branch `endpoint-evidence`, tool 1.5.0), answering
`docs/handoff/2026-11-03_endpoint_evidence_v3.md`.

Paste everything below the line into a Claude Code session started in
`~/Git/analytics_tools/direct_detect`.

---

## `flown_routes` v3 (adsb_historical_routes 1.5.0)

The new LAN file is `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930_v3.gpkg`. The v2 file
`2026_0714-0930.gpkg` is unchanged and stays your baseline. Its evidence report is
`2026_0714-0930_v3_evidence_report.md` in the same folder.

- `meta.schema_version` is `"3"`. Every v2 table, column, index and meaning stays; v3 only adds.
  Geometry is as v2 (MULTILINESTRING, split at the date line).
- `flight_id` is still `{aircraft_id}_{takeoff:%Y%m%dT%H%M%SZ}`, but `takeoff_time` is the first
  point of the track. A flight whose start POS extended (F2) therefore has an earlier takeoff, and
  a **different `flight_id`**, than in v2. Don't join v2 and v3 on `flight_id`; use aircraft and
  time overlap.
- **POS-only flights** (`disposition = 'pos_only'`, `sources = 'POS'`) have no ADS-B. Their points
  are the ACARS reports, minutes apart (`n_points_full` = reports; nothing is interpolated).
- **Unmapped fleets** (CC-, HC-, N- …): `aircraft_id` is **NULL**, `registration` is set (for
  example `CC-BMD`), and `flight_id` starts with the normalized registration (`CCBMD_…`). Join
  these on `registration`, normalized to `[A-Z0-9]`, as your OFP `tail` is.
- `disposition = 'kept_recovered'`: a segment 1.4.0 dropped (`invalid` / `no_airport_match`) and
  the new evidence completed.

### New `flights` columns

| Column | Meaning |
|---|---|
| `flight_number` | The ACARS `FI` number (for example `JJ3634`, IATA prefix) or, failing that, the matched planned leg's (`TAM3634`). Compare on the digits only: strip the 2-character IATA or 3-letter ICAO prefix and the leading zeros. |
| `flight_number_source` | `acars_fi`, `planned:<source>` (for example `planned:ofp`), or NULL. |
| `flight_number_conflict` | 1: POS reports with two different numbers fell inside the flight (the majority is kept). A mis-join signal. |
| `orig_source`, `dest_source` | Where each end came from; table below. |
| `orig_free`, `dest_free`, `orig_free_source`, `dest_free_source` | The end **without planned legs**, and its source. |
| `orig_candidates`, `dest_candidates` | `\|`-separated airports a route-time / geometry rescue accepted for that end, best first. |
| `endpoint_confidence` | `high` / `medium` / `low`, the lower of the two ends. |
| `endpoint_conflict` | 1: rotation or the planned leg contradicts an observed end (missing leg, tail swap or diversion). Also set on a 1.4.0 flight that the new evidence would have made invalid; it keeps its 1.4.0 ends. |
| `planned_leg_key` | The `leg_key` of your planned-legs CSV that matched, or NULL. |
| `n_pos_fixes` | POS points in the track. |

### `*_source` values, in evidence order (the first that applies wins)

| Value | Meaning | Confidence |
|---|---|---|
| `observed` | ADS-B saw the aircraft at the airport (1.4.0 detection). | high |
| `pos_observed` | A POS fix at the airport (within the airport radius and below the arrival / takeoff height). | medium |
| `planned:<src>:fn` | Your leg, rule 1: `flight_digits` = the ACARS number, same tail, takeoff in `[sched_dep − 60 min, sched_dep + 8 h]`. | high |
| `rotation` | The aircraft's next flight's observed origin (for a destination) or previous flight's observed destination (for an origin), ground gap 0–18 h. | medium |
| `planned:<src>:tail` | Your leg, rule 2: same tail, the window, and one observed end equal to the leg's (flights without a number). | medium |
| `route_time`, `geometry`, `bearing_join` | The 1.4.0 rescue. | low |
| `rotation_chained` | Rotation from a neighbour whose end is itself inferred. | low |

An end whose rescue had more than one candidate is `low` whatever its source. A leg match is used
only when it is unique both ways: one candidate leg for the flight, and no other flight with that
leg as a candidate. `ambiguous = 1` rows never match. A planned end never replaces an observed one.

### How to use `*_free`

`origin` / `dest` may have **come from your own OFP** (`planned:*`). When you measure how often
the builder agrees with the OFP, or count a flight as independently confirming its OFP, use
`orig_free` / `dest_free` (and their `*_free_source`). Otherwise you would count the plan as
agreeing with itself. For the exact-flight join:

1. Join on `planned_leg_key` where it is set (the builder already did tail + time + number, uniquely).
2. Otherwise join as today (tail, time, O/D) on `orig_free` / `dest_free`.
3. Treat `endpoint_conflict = 1` and `flight_number_conflict = 1` as mis-join candidates.

`rescue_method` and `disposition` keep their 1.4.0 meaning; `*_source` is the final answer.

### `meta` and `diagnostics`

- `meta.evidence_options` (JSON) gives the switches used; `meta.planned_legs` gives the files,
  row counts and date ranges; `meta.flight_number_prefixes` is the learned ACARS → plan prefix
  table (`{"JJ": {"TAM": n}, …}`); `meta.evidence_report` is the whole report as JSON.
- `diagnostics` rows with `phase = 'evidence'`: one per endpoint F2, F4 or F5 changed, with
  `evidence_feature`, `evidence_source`, `endpoint`, `before_code`, `after_code` and `flight_id`.

### Cautions

- 122 LAN v3 flights cross the date line (Chile–Australia/NZ, POS-only): read every part (see
  `direct_detect_flown_v2.md`).
- `rotation_chained` ends agree with the OFP only 37% (origins) and 52% (destinations); they are
  on POS-only and recovered flights only. Treat them as unknown.
- 99 OFP airports (domestic Colombia, Peru, Ecuador and a few others) are not in the builder's
  airport list, so no flight ends there; those legs cannot match.

### LAN results

See `2026_0714-0930_v3_evidence_report.md` and §13 of
`~/Git/kml/adsb_historical_routes/historical_route_geopackage_instructions_lan_v3.md`.
