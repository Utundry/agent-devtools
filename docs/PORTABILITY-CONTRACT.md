# Portability contract

Agent DevTools MUST:

- run with Python standard library only for its base capabilities;
- require no background service or network connection;
- keep all unique project knowledge outside disposable caches;
- work after `.agent-cache/` and `.agent-work/` are deleted;
- avoid modifying application source merely to make verification pass;
- provide provenance for selected checks/context/artifacts;
- bound subprocess lifetime and agent-facing output;
- support project-specific policy declaratively where practical;
- treat unknown/unsafe verification classification conservatively.

Agent DevTools SHOULD:

- work without Git for basic operations;
- work without project configuration using sane discovery defaults;
- update indexes/evidence incrementally;
- keep cold indexing for small/medium repositories in seconds;
- keep warm context ensure sub-second where practical;
- keep context databases in MB/tens of MB rather than hundreds of MB for ordinary projects.

Agent DevTools MUST NOT require Qdrant, embeddings, Redis, Docker, FastAPI, an ORM, a daemon or a model runtime for its core use cases.
