# Route Builder — tool-shell extension spec

This app (`route_builder/`) is built as a **generic local tool-runner shell** with exactly
one tool — `adsb_historical_routes.py` — registered in it today. The shell itself (HTTP
server, SSE run console, browse modal, presets, manifest) does not know anything specific
about flight routes; it only knows how to render a parameter catalog into a form, turn the
submitted values into a subprocess argv, and stream that subprocess's stdout back to the
browser. This document is the contract a **new sibling tool** — the planned airport-file
builder, route-time builder, and ICAO↔hex tail matcher — needs to satisfy to be hosted by
this same shell, ideally with no changes to `server.py`'s HTTP layer or `index.html`'s
layout code.

Reference implementation: `route_builder/server.py`. Read it alongside this doc — the
functions named below (`build_argv`, `validate`, `parse_summary`, `classify_line`,
`make_server`) are the concrete example every new tool should follow the shape of.

## a. What a "tool" is

A tool is the tuple:

```
{
  id:          short slug, e.g. "route_builder", "airport_builder", "tail_matcher"
  title:       display name for the topbar / tab, e.g. "ADSB Historical Route Builder"
  parameters:  a parameters.json-style catalog (see (d) below) — one entry per form field
  command:     { script: path to the target stdlib script,
                 build_argv: params dict -> argv list, mirroring build_argv() below }
  manifest:    a per-tool section under the shared manifest (see (e) below) —
               its own roots/defaults/presets
}
```

Today's single tool is implicit rather than an explicit registry entry: `route_builder/`
*is* the `route_builder` tool, wired directly into `server.py`. `SCRIPT_PATH` (server.py:59)
is the `command.script`, `build_argv()` (server.py:84) is `command.build_argv`, and
`route_builder/parameters.json` is `parameters`. A second tool should follow the same
three-piece shape — its own script, its own `build_argv`-shaped function, its own
parameter catalog file — so the shell can be generalized into an actual `{id -> tool}`
registry later without redesigning any one tool.

## b. The server endpoints are tool-agnostic

None of the HTTP endpoints in `make_server()` (server.py:365) reference route-splitting
concepts by name. Each one operates purely on the generic pieces — a params dict, an argv
list, a manifest, a folder path — so a new tool reuses every endpoint unchanged, as long as
it supplies its own catalog, argv mapping, and summary-parse regexes:

| Endpoint | What it does, generically | Where a new tool plugs in |
|---|---|---|
| `GET /` | Serves `index.html` (server.py:402-404) | shared frontend shell (see (c)) |
| `GET /parameters.json` | Serves the raw catalog (server.py:405-406) | swap in the new tool's catalog file |
| `GET /api/config` | Manifest roots/defaults/presets + catalog (server.py:407-410) | reads the new tool's manifest section |
| `GET /api/browse?path=` | Folder listing for the picker, `.kml`-count aware (server.py:339-354, 411-413) | reused verbatim — it just lists a directory |
| `GET /api/outputs?dir=` | Lists `.kml` outputs in a folder (server.py:357-362, 414-415) | reused verbatim, or generalize the extension filter if a tool's outputs aren't KML |
| `GET /api/run/<id>/stream` | SSE stream of `line`/`progress`/`phase`/`done`/`error` events (server.py:416-417, 483-501) | reused verbatim; only `classify_line`/`parse_summary` differ per tool |
| `POST /api/validate` | Confines + checks paths, returns `{errors, warnings, argv, counts}` (server.py:177-248, 428-429) | new tool supplies its own `build_argv` + its own required-field checks, following `validate()`'s shape |
| `POST /api/presets` | Add a named preset to the manifest (server.py:430-431, 462-473) | reused verbatim against the tool's manifest section |
| `POST /api/run` | Validates then spawns the subprocess via `RunManager.start(build_argv(...))` (server.py:432-438) | new tool passes its own `build_argv` output |
| `POST /api/run/<id>/cancel` | Terminates the subprocess (server.py:439-440, 325-329) | reused verbatim |
| `POST /api/run/<id>/reveal` | `open -R` on the finished output file (server.py:441-442, 503-510) | reused verbatim |
| `DELETE /api/presets?name=` | Removes a non-builtin preset, 409 on builtin (server.py:449-460, 475-481) | reused verbatim against the tool's manifest section |

The one per-tool seam inside the HTTP layer is **what `build_argv` and `validate` do with
the params dict**, and **what `classify_line`/`parse_summary` extract from that tool's own
stdout format** (`RunManager._pump`, server.py:279-313, calls both). A new tool supplies:

1. Its own `build_argv(params) -> List[str]` (mirrors `_ARGV_MAP`/`_BOOL_FLAGS` + the
   `build_argv` function at server.py:63-105) mapping catalog keys to the new script's CLI
   flags.
2. Its own `validate(params, allowed_roots) -> dict` (mirrors server.py:177-248) checking
   the new tool's required inputs/outputs and returning the same
   `{kml_count-ish counts, output_path, output_exists, argv, warnings, errors}` shape so the
   frontend's pre-flight card (see (c)) renders unchanged.
3. Its own `classify_line(line) -> (kind, payload)` (mirrors server.py:124-135) — its script's
   stdout won't say `"Processing:"` or match `_PHASE_PREFIXES`, so this needs new patterns
   for that script's progress/phase lines.
4. Its own `parse_summary(text) -> dict` (mirrors server.py:138-159) — regexes over that
   script's final printed summary, feeding the frontend's results panel.

Everything else in `RunManager` (job bookkeeping, SSE event names `line`/`progress`/`phase`/
`done`/`error`, cancel-via-terminate) is generic and unconditionally reusable.

## c. The frontend shell is reused; a tool tab is added for tool #2

`index.html` today renders one tool because `CONFIG` (loaded from `/api/config`) only ever
describes one catalog. Its structure is already generic:

- **Topbar** — app title + theme toggle; not tool-specific.
- **Presets bar** — reads `CONFIG.presets`, calls `POST/DELETE /api/presets`; already
  data-driven off the manifest, not the route-splitting domain.
- **Form-from-catalog** — the field list is built purely from `CONFIG.parameters`
  (`route_builder/parameters.json`), grouped by each entry's `group` and rendered per its
  `type` (folder/file/files/text/number/slider/toggle/segmented/color). A new tool that
  ships a conforming catalog gets a full working form with zero frontend code changes.
- **Browse modal** — talks to `GET /api/browse`, which is tool-agnostic (b).
- **Run console** — subscribes to the SSE stream, renders `line`/`progress`/`phase`/`done`/
  `error` events generically; the pre-flight card renders whatever `validate()` returns.

When a second tool is registered, the shell gains a **tool tab** in the topbar (one tab per
`{id, title}`) that switches which catalog/manifest-section/script `CONFIG` describes and
re-renders the form; the presets bar, browse modal, and run console do not need to change,
since they already operate on whatever the active tool's `CONFIG` contains. Until a second
tool exists, the topbar has no tab strip — that is the only piece of frontend UI this spec
expects a second tool to add.

## d. The `parameters.json` schema

Each entry in a catalog file (see `route_builder/parameters.json` for the reference, and
`tests/test_route_builder_server.py::test_parameters_catalog_complete` for the enforced
invariants) is a flat JSON object with these keys:

| Key | Type | Meaning |
|---|---|---|
| `key` | string | The params-dict key the frontend/server pass around internally (e.g. `"corridor_cross_track_km"`). Unique per catalog. |
| `flag` | string or `null` | The CLI flag this maps to (e.g. `"--corridor-cross-track-km"`). `null` for fields the server derives instead of passing straight through (e.g. `output_dir`/`output_name`, which `build_argv` joins into a single `--output`). |
| `group` | string | Which form section the field renders under (e.g. `"input"`, `"output"`, `"detection"`, `"styling"`). Purely a UI grouping key — a new tool defines its own group names. |
| `type` | one of `folder`, `file`, `files`, `text`, `number`, `slider`, `toggle`, `segmented`, `color` | Which form control renders (folder/file pickers open the browse modal; `segmented` is a small enum picker; `color` expects a KML `AABBGGRR` hex string). |
| `label` | string | Human-readable field label shown in the form. |
| `default` | any (string/number/bool/`null`) | The value the field is seeded with; also used by `build_argv`-style functions to decide whether a value is "non-default" and therefore worth emitting as a CLI flag. |
| `help` | string | Required, non-empty. One-line explanation shown as help text under the field. |
| `example` | string | Required, non-empty. A concrete example value/usage, shown as placeholder/hint text. |

A conforming catalog is any JSON array of objects satisfying that shape — the test above is
effectively the schema's executable spec: every entry needs `help` and `example`, and
`type` must be one of the eight control kinds the frontend knows how to render.

## e. How `manifest.default.json` gains a per-tool section

Today's `manifest.default.json` has one flat set of `roots`/`defaults`/`presets`, because
there is one tool. To host a second tool without breaking the first, restructure so each
tool owns its own `roots`/`defaults`/`presets`, keyed by the tool's `id`, while `version`
and `server` stay shared:

```json
{
  "version": "1.0.0",
  "server": { "port": 8765 },
  "tools": {
    "route_builder": {
      "roots": { "input_default": "...", "airports_dir": "...", "...": "..." },
      "defaults": { "confidence": "permissive", "...": "..." },
      "presets": [ { "name": "Recommended — clean & complete", "builtin": true, "params": {} } ]
    },
    "airport_builder": {
      "roots": { "...": "..." },
      "defaults": { "...": "..." },
      "presets": [ ]
    }
  }
}
```

`load_manifest()` (server.py:27-40) would resolve `~`/relative paths per-tool the same way
it does today (it already loops over a fixed set of root keys and resolves each), and
`GET /api/config` would take a `tool` query parameter (defaulting to the first/only tool)
and return that tool's `roots`/`defaults`/`presets` plus its `parameters` catalog. Preset
add/delete (`_add_preset`/`_del_preset`, server.py:462-481) would look up and mutate
`man["tools"][tool_id]["presets"]` instead of the top-level `presets` list, but the
mutate-persist-return shape is otherwise unchanged. `allowed_roots` can stay a single
shared list at the top level (confinement is a security boundary, not a per-tool concern)
or move under each tool if a future tool needs a materially different sandbox — either way,
`confine_path()` (server.py:49-56) itself needs no change, since it only ever takes a
`List[str]` of roots.

This is a one-time, additive migration: nothing in this spec requires it to happen before a
second tool exists — the flat manifest works fine for one tool — but a new tool should be
written expecting the nested `tools.<id>.*` shape described here, since that is what keeps
`load_manifest`/`save_manifest`/the preset endpoints tool-agnostic long-term.

## Summary: adding a new sibling tool

1. Write the new tool's stdlib script (e.g. `build_airport_file.py`), exactly as
   `adsb_historical_routes.py` is today: a CLI script with real argparse flags and
   informative stdout (progress lines + a final summary block).
2. Write its `parameters.json`-shaped catalog (d), covering every flag with `help` +
   `example`.
3. Write its `build_argv`/`validate`/`classify_line`/`parse_summary` following the shapes
   in (b), reusing `confine_path`/`_count_kml`/`_count_csv_rows`-style helpers from
   `server.py` where they already fit.
4. Add a `tools.<id>` section to `manifest.default.json` (e) with that tool's
   roots/defaults/presets.
5. Wire the tool into the (generalized) server registry and give `index.html` a tab for it
   (c) — everything else (HTTP endpoints, SSE run console, browse modal, presets bar) is
   already generic and requires no change.
