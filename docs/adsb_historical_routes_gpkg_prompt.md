# Kickoff prompt: adsb_historical_routes, GeoPackage output

## Files to copy into `~/Git/kml/adsb_historical_routes/docs/specs/`
- `~/Git/qgis/docs/specs/2026-10-27-route-gpkg-output-spec.md`, the spec. Use §3, §4, §6, §7, §8, §8.1, §9, §11.1 and §12.
- `~/Git/qgis/docs/geopackage_build_instructions.md`, which shows where this tool sits in the chain.

## Prompt to paste into a new session in `~/Git/kml/adsb_historical_routes`

```
Add GeoPackage output to this tool, following docs/specs/2026-10-27-route-gpkg-output-spec.md.
Read docs/geopackage_build_instructions.md first for context: this tool's GeoPackage will be
the input for the QGIS viewer (~/Git/qgis) and for direct_detect
(~/Git/analytics_tools/direct_detect).

The sections that apply here:
- §3: shared conventions.
- §4: the `flown_routes` v1 schema.
- §6: KML from GeoPackage.
- §7: delivery order.
- §8 and §8.1: acceptance, on POE and on VIV.
- §11.1: findings from the VIV data, which must be fixed.
- §12: output selection, `--format both|gpkg|kml`, default `both`, plus a standalone `gpkg_to_kml`.

Must-haves:
- KML output stays identical when the format is `kml` or `both`, proven by the §6 equivalence test
  in every mode (default, `--color-mode altitude`, `--stitch-gaps`, `--integrate-pos`,
  `--group origin`).
- One `flights` row per flight, not per placemark. Per-source runs go only in `track_points.source`
  and `tracks_full`. `tracks_full` is required.
- Origin/destination mislabels: about 2.7% of the VIV October flights fly more than 2.5× the
  great-circle distance between their labelled airports, for example MMTO→MMSM with 131 flights
  of 2½–10 hours. Find and fix the root cause in airport assignment. Also add `od_gc_km`,
  `od_distance_ratio` and the `suspect_od` flag (threshold 2.5, configurable) and count them in the
  diagnostics.
- Name outputs and `meta` `date_from`/`date_to` from the actual first and last flight dates.
- Stay standard-library only at runtime: no GDAL or geopandas. The GeoPackage writer is a small
  module using `sqlite3` and WKB. If `~/Git/kml/company_route_extractor` already has the writer
  module, copy (vendor) it rather than writing a second one.
- The output must open in QGIS 3.44 without warnings.

Rules:
- Don't modify other projects. Write up anything they need.
- Never commit data: KML, GeoPackage, CSV outputs, or anything in `input/` or `output/`.
- Ask me before pushing or creating PRs.

Start by:
1. Reading the code and the spec.
2. Listing any question whose answer would change the plan.
3. Writing an implementation plan, and stopping for my review.
```
