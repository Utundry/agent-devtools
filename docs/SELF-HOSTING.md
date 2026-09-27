# Self-hosting policy

Agent DevTools should use its own released capabilities on its own source as soon as each capability is mature enough to do so without circular fake tests.

Current matrix:

| Capability | Self-host status | Evidence |
| --- | --- | --- |
| `agent check` selection/runner/cache/adapters | active | repository `agent-tools.json` + ordinary cold/warm runs |
| `agent check certify` | active | self-hosted cold certification followed by certified-evidence reuse |
| preset loading/composition | active through own test suite | every bundled preset materializes through normal config/policy parsers |
| file-set/output contracts | active through own integration tests | tests are executed by the repository's own `agent check` |
| `agent check replay` | active | A5 release snapshot is replayed into the current tree and cold-certified |
| `agent release` | active | B1 self-release from A6 passed; each subsequent release uses the previous released source package as its base |
| `agent context` | active | self-index ensure/query/inspect plus affected-context retrieval run against Agent DevTools; deleting the DB is a supported recovery path |

Qualification command for the current source:

```bash
python agent.py check run --profile full --changed agent.py --no-cache
python agent.py check run --profile full --changed agent.py
python agent.py check certify --cold
python agent.py check certify
python agent.py check replay --base previous-source.zip --bundle-out exact-change.agent-replay.zip
python agent.py release build --version <version> --base previous-source.zip --out-dir ../release
python agent.py release verify ../release
python agent.py context ensure --rebuild
python agent.py context query "release preset runtime"
python agent.py context affected --changed agent_devtools/context/index.py
python agent.py context inspect context.index.lifecycle
```

The first command proves physical execution. The second proves ordinary content-addressed reuse. Self-hosting must never special-case the Agent DevTools repository inside the engine.
