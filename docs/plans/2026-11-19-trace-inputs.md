# Trace inputs: phases

Source: [`docs/handoff/2026-11-19_trace_json_sources.md`](../handoff/2026-11-19_trace_json_sources.md).

## Phase 1: read every trace input type (done, 1.6.0)

- **Goal:** read ADS-B Exchange JSON, adsb.lol, and the S3 trace builder's JSON / KML through
  `--trace-folder`, with today's results from the same data.
- **Delivered:**
  - `parse_trace_json`, `discover_trace_folder`, `--trace-folder`, `--trace-source`;
  - `meta.track_input`, `meta.track_source`, `meta.trace_folder`;
  - FlightAware fields read from S3 `tf` points.
- **Exit check:**
  - `tests/test_trace_json.py`: synthetic files for every type, and JSON and KML of the same
    day giving the same flights;
  - the optional real pair in `tests/fixtures/real/` (not committed);
  - the 1.4.0 equivalence tests unchanged;
  - an AMX 3-day smoke build from the real JSON folder.
- **Not available to test against:** adsb.lol and S3 builder outputs. Both projects have no
  output yet; their files are built from their designs (same readsb layout; builder §5.4).
  Re-check once each publishes real files.

## Phase 2: choose the best source per flight (to brainstorm first)

Model and effort: opus, high (design), then sonnet, medium (build).

Questions to settle with the owner (handoff §8):

1. **Inputs.** A repeatable `--trace-folder LABEL=PATH`, or one flag per source.
2. **Unit of choice.** A primary track per tail-day, with the others filling its gaps through
   `merge_track_with_sources()`; or a choice per flight, after detecting flights in each source
   and matching them by time overlap.
3. **Score.**
   - Coverage: minutes with a point within 2 min.
   - Longest gap.
   - Takeoff and landing seen.
   - Measured per tail-day or per flight, never ranked by source name.
4. **Output.**
   - `flights.track_source` (which source each flight came from).
   - Per-point `source` values beyond `ADSB` / `POS` / `FIREHOSE`; consumers (`direct_detect`,
     qgis) read `ADSB` today.
5. **Never interleave** two dense sources point by point. Clock offsets and jitter make
   zig-zags, so fill gaps only.
