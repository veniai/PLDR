# PLDR P1 Reliable Collection Workbench Prototype

## Purpose

This prototype explores the approved P1 front-end direction without changing the
runtime application, API, database, or deployment. It turns the existing map-first
demo shell into an attention-queue-driven analyst workbench while preserving PLDR's
evidence-first visual language and human confirmation boundary.

## Assumptions

- P1 remains a single-analyst pilot. Metrics use a selected time range rather than
  claiming per-user "since last login" semantics.
- Fixed URL/RSS/API collection, external keyword discovery, and manual material
  submission remain parallel inputs into the same controlled intake boundary.
- AI output is always a candidate. Confirmed objects remain visually and
  behaviorally separate.
- The pilot starts with a small maritime/logistics topic pack and representative
  mock sources. All content in this prototype is illustrative.
- Map display is secondary and only represents events with known coordinates.

## Included screens

1. Today: new materials, meaningful changes, pending review, and collection failures.
2. Sources: schedules, execution health, retries, and source run history.
3. Changes: immutable snapshot versions and readable V3/V4 comparison.
4. Review: queue, source material/diff, candidate editing, and human-readable impact preview.
5. Event dossier: confirmed claims, evidence, source independence, and recent activity.
6. Responsive narrow-screen review flow.

## Visual source

The prototype extracts and refines tokens and interaction vocabulary from:

- `apps/dashboard/index.html`
- `apps/dashboard/assets/styles.css`
- `apps/dashboard/assets/app.js`

It keeps the navy/cyan PLDR identity, semantic warning colors, compact panels,
source health, and evidence trace affordances. It deliberately increases body text
size, reduces decorative glow, makes the map secondary, and replaces modal-heavy
flows with persistent routes.

## External references (interaction patterns only)

- World Monitor: situational overview, source freshness, visible degradation.
- changedetection.io: watch table, run history, version comparison.
- ArchiveBox: immutable snapshot and capture artifact vocabulary.
- OpenCTI/OpenAleph: object dossiers, scoped search, and investigation structure.
- NewsNow: high-density readable source lists.
- BettaFish/MiroFlow: visible task stages and report artifacts, reserved for later phases.

No third-party source code or visual assets are copied into this prototype.

## Open locally

The JSX files are transpiled in the browser, so serve the directory over HTTP:

```bash
python3 -m http.server 4311 --directory designs
```

Then open `http://127.0.0.1:4311/pldr-p1-workbench/prototype.html`.
The first load needs network access for the pinned React and Babel browser builds.
For a single-file handoff, open `PLDR-P1-Workbench-Standalone.html` directly;
it contains the same CSS, mock data, and JSX inline (the pinned libraries still
load from the network).

Useful flows to try:

- Choose **添加输入 → 关键词发现** to see search positioned beside fixed
  URLs, APIs, pasted text, and files rather than replacing them.
- Open **网页变化**, switch between inline/split Diff, then send the change to
  **待审箱**.
- In **待审箱**, compare the fixed source text, edit a candidate, preview the
  human-readable impact, and confirm it into the event dossier.
- Resize to 390 px to use the three-step narrow-screen review flow.
