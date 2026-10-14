# Source evidence without Git

If Git change discovery is unavailable, verification binds the executed command to a bounded, content-hashed project source snapshot. A source edit changes its fingerprint, and later assessment becomes `stale`. Runtime, dependency, build and knowledge directories are excluded to avoid self-modifying evidence. Scans fail closed at safety limits.

A directory fingerprint is **not** a diff and cannot identify affected tests. `relevance.status` remains `unknown` and no claim of affected-plan coverage is made. The fingerprint binds observed files selected by the lightweight source filter, not arbitrary binary assets; explicitly record separate evidence for assets and external dependencies when material.
