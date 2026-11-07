# Kickoff prompt: endpoint evidence + LAN v3 rebuild

Start a new Claude Code session in `~/Git/kml/adsb_historical_routes` and paste everything inside
the block below.

```
Implement the request in docs/handoff/2026-11-03_endpoint_evidence_v3.md, then rebuild the LAN
GeoPackage and its KML as its §7 says.

Read first, in this order:
1. docs/handoff/2026-11-03_endpoint_evidence_v3.md: the request (principles, features F1-F9,
   schema v3, acceptance, the LAN build, known limits).
2. historical_route_geopackage_instructions_lan.md: how the current LAN file (1.4.0) was built.
   §5 is the POS format and §11 the data limits this request addresses.
3. docs/GEOPACKAGE.md: the flown_routes v2 schema you are extending.

Rules:
- The core stays the per-hex 24-hour ADS-B stream, cut into flights between airports by
  position, elevation and route time. POS then fills coverage gaps by tail and time. A flight
  number or planned leg is optional evidence and is never required to keep a flight.
- Every new behaviour is behind its own flag, off by default. With all flags off the output must
  equal 1.4.0; prove it with the existing tests plus an equivalence test.
- Work on a new branch `endpoint-evidence` from `gpkg-output`. TDD: a failing synthetic test
  first for each feature. Run `python3 -m pytest -q` before each commit. Do not commit the
  untracked files already in input/ (CEB / GLO / VIV Cirium files); they are not part of this
  work.
- Never overwrite /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg: it is direct_detect's
  baseline. The new file is 2026_0714-0930_v3.gpkg.
- The POS folder holds 2.07 M files. Read it with os.scandir, never ls, and only once per run.
- Before the full LAN build, run on a short window (for example 2026-08-01..08-03) and check the
  §6 "expected direction" items. If they do not hold, stop and report to me before the full build.
- Ask me before changing the evidence order (F8), any default, or anything the request marks
  as the owner's.

When done:
- the v3 GeoPackage, summary, validation and evidence report are in /Volumes/jy-ext/route_gpkg/lan/;
- the KML is in /Volumes/jy-ext/kml/kml-processed/adsb_historical_routes_output/lan_routes_2026_0714-0930_v3.kml;
- historical_route_geopackage_instructions_lan_v3.md (new guide), docs/GEOPACKAGE.md (v3) and
  docs/handoff/direct_detect_flown_v3.md (for direct_detect) are written.

Then give me a short summary covering:
- flights v2 → v3;
- endpoints by source and confidence;
- the agreement table;
- POS-only flights by fleet;
- dropped segments recovered;
- build time and file size.
```

## Files this prompt relies on

| File | Path |
|---|---|
| Request | `~/Git/kml/adsb_historical_routes/docs/handoff/2026-11-03_endpoint_evidence_v3.md` |
| Current LAN guide | `~/Git/kml/adsb_historical_routes/historical_route_geopackage_instructions_lan.md` |
| Planned legs (OFP) | `/Volumes/jy-ext/data_lan/trial_analysis/planned_legs/lan_ofp_legs_2026-07-01_2026-09-08.csv`, 124,390 legs, from direct_detect (`python -m direct_detect.ingest.planned_legs`) |
| POS | `/Volumes/jy-ext/data_lan/trial_analysis/pos/`, 2,074,428 files, all LATAM fleets |
| Baseline (do not overwrite) | `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg` |

## After it finishes

Tell the direct_detect session. It will:
- read `docs/handoff/direct_detect_flown_v3.md`;
- point `config/lan.toml` `tracks_gpkg` at the v3 file and re-index it into a new
  `data/flown_lan_v3.sqlite`, keeping the v2 index as the baseline;
- teach the indexer the v3 columns (`flight_number`, `*_source`, `*_free`, `endpoint_confidence`,
  `pos_only`);
- treat a planned-leg endpoint as OFP-derived, not as independent agreement (A-72);
- rerun the LAN analysis into a new run folder and compare match rates with the 1.4.0 baseline.
