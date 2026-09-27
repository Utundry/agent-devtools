# Stage B2 — self-release qualification

Stage B2 is an acceptance gate rather than a separate implementation fork. The Stage B1 release engine is considered usable only after Agent DevTools can release itself from the previous certified source snapshot using the same public `agent release` facade exposed to consumers.

The qualification sequence is:

```bash
python agent.py release build \
  --version <current-version> \
  --base <previous-source.zip> \
  --out-dir <release-dir>

python agent.py release verify <release-dir>
```

The self-release must prove:

- the previous source package is a valid replay base;
- exact replay reconstructs the current canonical source byte-for-byte;
- normal target certification and cold replay certification pass;
- deterministic package manifests and hashes are produced;
- the staged release verifies itself before publication;
- the released source package can serve as the next release base.

No self-host special case exists in the engine. Agent DevTools is just another consumer repository with declarative configuration.
