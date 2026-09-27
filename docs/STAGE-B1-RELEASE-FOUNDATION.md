# Stage B1 — portable release foundation

Stage B1 turns the already-proven Check/Replay primitives into a reusable release packager without introducing deployment-provider logic.

## Command

```bash
python agent.py release build \
  --version 1.2.3 \
  --base previous-source.zip \
  --out-dir ../release-1.2.3

python agent.py release verify ../release-1.2.3
```

`release build` does not create an independent correctness path. It reuses the existing foundations:

1. normal exact replay certifies the target and cold-certifies the replayed source tree;
2. package inventories are frozen only after verification/build suites have produced their declared generated outputs;
3. deterministic package ZIPs are written with fixed metadata and no compressor dependence;
4. every package receives a sibling content manifest with per-file SHA-256 and an aggregate fingerprint;
5. the exact replay bundle is shipped alongside the packages;
6. `RELEASE-EVIDENCE.json` and `SHA256SUMS.txt` bind the artifact set together;
7. the complete staged release verifies itself before one atomic directory rename publishes it.

A failed build never intentionally publishes a partially assembled release directory.

## Declarative configuration

```json
{
  "release": {
    "artifactPrefix": "my-project",
    "packages": [
      {
        "id": "source",
        "kind": "source"
      },
      {
        "id": "runtime",
        "kind": "files",
        "include": ["public/**", "backend/**"],
        "exclude": ["backend/tests/**"],
        "required": ["public/index.html"]
      }
    ]
  }
}
```

`kind: source` uses the exact canonical source identity already defined by `check.replay`. There may be at most one source package.

`kind: files` packages an arbitrary post-build file set. This is intended for runtime/deployment packages. `required` guards against accidentally publishing an empty or incomplete package.

No framework-specific package code exists in the engine.

## Determinism

Package ZIPs:

- sort paths;
- use fixed ZIP timestamps;
- use `ZIP_STORED`;
- normalize archive file mode metadata;
- are based on a frozen path→SHA-256 inventory.

Two builds from byte-identical verified target state and the same version/base produce identical release package bytes, replay bundle, evidence and checksum list.

## Verification

`agent release verify` checks:

- exact replay artifact SHA-256;
- each package manifest SHA-256;
- each package archive SHA-256;
- archive contents against the per-file manifest;
- aggregate package fingerprint;
- every entry in `SHA256SUMS.txt`.

This is integrity/reproducibility evidence, not a cryptographic signature. Signing remains a possible later optional layer.

## Boundary

Stage B1 deliberately does **not** implement:

- SSH/FTP/HTTP deployment;
- package registry upload;
- cloud-provider SDKs;
- rollback orchestration;
- secret management;
- mandatory Git patch export.

Those are consumer/deployment adapters, not portable release-core responsibilities.
