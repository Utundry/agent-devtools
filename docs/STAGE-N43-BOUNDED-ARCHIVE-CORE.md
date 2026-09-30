# Stage N4.3 — Bounded Verified Archive Core

Portable state and exact replay now share one stdlib-only archive safety primitive.

The common reader/extractor rejects duplicate file members, unsafe paths, symlink entries and encrypted members, and enforces explicit limits on entry count, one-entry uncompressed size, and total uncompressed payload before accepting archive contents.

This removes divergent ZIP-reading behavior between handoff, checkpoints, workspace snapshots and replay, while keeping format-specific manifest and semantic validation in each domain module.

Replay base extraction no longer calls `ZipFile.extractall`; validated members are streamed to bounded project-relative destinations.
