# Transfer procedure

1. Unpack this archive into the root of the new `agent-devtools` repository.
2. Commit the migration baseline before refactoring donor code.
3. Run:

```bash
python agent.py doctor
python -m unittest discover -s tests -v
python scripts/verify_kit.py
```

4. Treat the historical private donor as read-only external evidence during Stage A.
5. Implement portable code beside it, adding parity tests before deleting or reorganizing donor assumptions.
6. Do not commit `.agent-cache/` or `.agent-work/`.
7. Do not vendor the 20 MB Organizer `node_modules` bundle into this repository. It is a consumer-specific test dependency, not Agent DevTools source.

Private consumer snapshots are not distributed in the public repository.
