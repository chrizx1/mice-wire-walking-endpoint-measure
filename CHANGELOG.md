# Changes

## 1.1.1 — 2026-11-05

- Fixed a proposal-table layout bug: detected rows could be hidden behind the containing panel even though the proposal count was shown. The table is now a direct child of its display container.
- Clarified the initial unscanned state and added a rendered-widget regression check.

## 1.1 — 2026-11-05

- Added optional dark-mouse movement proposals using the calibrated rod axis.
- Added a background scan with progress and cancellation; no extra dependencies.
- Added video-assisted review with interval editing, addition, removal, boundary/frame checks, and mandatory verification of every retained interval.
- Preserved the original manual editor, timestamp CSV import, video preview, playback, frame stepping, and endpoint-measurement workflow.
- Saved detection settings and verification provenance with projects. Manual edits after acceptance are recorded.
- Retained support for existing project files. Endpoint distance/speed calculations are unchanged.

The detector provides suggestions for dark-mouse/light-background recordings. It does not replace behavioral review and can miss movement or propose posture/handling artifacts.
