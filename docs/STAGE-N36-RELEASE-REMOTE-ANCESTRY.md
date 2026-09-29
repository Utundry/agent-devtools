# Stage N3.6 — Fast-forward-aware release publication

Self-host publication of 0.8.10 exposed an overly strict remote preflight:
the publisher required local `HEAD` to equal `origin/main`. That rejects the
normal zero-manual-Git release case where validated source commits exist
locally and the remote branch is their ancestor.

N3.6 changes the publication invariant from equality to ancestry:

- equal local/remote heads are valid;
- local ahead is valid only when the fetched remote head is an ancestor of local `HEAD`;
- local behind or diverged histories remain blocked;
- remote release-tag collision remains blocked;
- publication still uses one atomic branch+tag push.
