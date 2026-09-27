# Stage A6 — exact replay

Stage A6 completes Agent Check extraction with a zero-Git exact replay proof.

The proof answers one narrow question:

> Given a known base source snapshot and the current target tree, can Agent DevTools reconstruct exactly the same canonical source bytes and, when semantic verification is enabled, reproduce the declared generated output bytes?

## Command

```bash
python agent.py check replay \
  --base previous-source.zip \
  --bundle-out exact-change.agent-replay.zip
```

By default replay:

1. incrementally certifies the target through the normal A5 certified-evidence path;
2. inventories canonical source bytes;
3. builds a deterministic, binary-safe replay bundle;
4. materializes the known base into a disposable directory;
5. verifies the base fingerprint before mutation;
6. applies additions, changes and deletions from the bundle;
7. proves target/replay canonical source equality;
8. attaches declared `check.replay.sharedPaths` such as dependency directories;
9. cold-certifies the replayed tree;
10. compares every declared `outputs.capture` byte hash between target and replay.

`--source-only` skips semantic certification/generated-output comparison and proves only exact canonical source reconstruction.

## Why a replay bundle instead of requiring Git patch

Stage A deliberately keeps Git optional. The replay artifact is therefore a tiny ZIP container with:

- canonical JSON manifest;
- base/target source fingerprints;
- explicit added/changed/deleted paths;
- exact bytes for added/changed files;
- SHA-256 for every payload file.

ZIP entries use fixed metadata and no compression, so identical inputs produce identical bundle bytes without depending on a particular Git or zlib build. Binary files are first-class; no text diff parser is involved.

A conventional Git patch may be added later as an optional Stage B release export. It is not needed for the reproducibility proof itself.

## Configuration

```json
{
  "check": {
    "replay": {
      "sourceInclude": ["**"],
      "sourceExclude": ["MANIFEST.sha256", "VALIDATION.txt"],
      "sharedPaths": ["node_modules"]
    }
  }
}
```

`sourceInclude` defaults to the complete project tree. Global `ignore` rules and `sourceExclude` remove disposable/generated state from canonical source identity.

`sharedPaths` are never bundled into canonical source. They are attached from the certified target tree only for replay verification. Presets use this for dependency trees such as `vendor`, `node_modules`, and `frontend/node_modules`.

## Safety

Replay fails closed when:

- the base snapshot is absent or malformed;
- archive entries escape the extraction directory;
- the actual base fingerprint differs from the bundle's certified base fingerprint;
- a payload file is absent or has the wrong SHA-256;
- reconstructed canonical source differs from the target;
- target certification fails;
- replay cold certification fails;
- captured generated output bytes differ.

Symlinks are currently excluded from canonical source unless explicitly ignored/excluded. This is intentional: silently dereferencing them would not be an exact source proof.

## Stage boundary

A6 does not package or deploy a release. It proves source/generated reproducibility. Stage B can now reuse this exact-replay primitive for release snapshots, optional Git patch export, runtime packaging, hashes and release evidence.
