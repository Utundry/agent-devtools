# One-command source update and publication

Use the same Python + Git release workflow through a reusable launcher:

```bash
python scripts/update_release.py --version 0.8.12 --publish
```

The launcher also works as a standalone downloaded file, from any directory:

```bash
python /path/to/update-agent-devtools.py --repo /path/to/agent-devtools --version 0.8.12 --publish
```

`--repo` may point to another worktree of the repository. The launcher locates
the checked-out `main` worktree; use `--branch` for a different publication
branch. It prepares **committed** source in a clean clone under the original
worktree's ignored `build/` directory, synchronizes non-diverged history with
the remote, and invokes the existing `scripts/build_release.py`. That builder
owns version changes, bootstrap generation, qualification, commit, annotated
tag, atomic branch/tag push, and remote verification. No alternate publisher
or Python dependency is introduced.

To incorporate a supplied binary patch in the same invocation:

```bash
python /path/to/update-agent-devtools.py --repo /path/to/agent-devtools --patch /path/to/update.patch --version 0.8.13 --publish
```

The patch is checked and applied only in the clone. An exactly applied patch
is recognized; an incompatible committed base is rejected without changing
the original tree. Existing uncommitted partial fixes do not obstruct patch
preparation. They are not silently treated as source changes to publish.
The accepted patch SHA-256 is recorded in Git history, so a repeated command
can recognize it even after the publisher changes version/bootstrap files.

After publication, the original worktree receives a fast-forward. Unrelated
local edits, installed `devtools/`, and bootstrap reports stay in place. If
local edits or untracked files obstruct the fast-forward, Git saves them in
a new stash including untracked files; the launcher prints its exact object
ID. Existing stash entries remain. The backup is retained without automatic
apply, since old partial fixes may conflict with the newly published source.
The updater never force-pushes, resets the original worktree, or drops a stash.

Without `--publish`, preparation and qualification run, and artifacts are
saved locally; neither remote nor original branch is updated. A failed build
retains the preparation clone and log. A failed synchronization after a
successful push reports an error; rerunning the command recognizes the remote
tag and can finish synchronization without another build. Diverged histories
and in-progress Git operations stop the update.

Use a **new version** when source already records an unpublished candidate:
for example, publish `0.8.12` from `0.8.12-rc.1`. The canonical builder retains
its existing prohibition on publishing the exact current source version.

Successful runs save `build/update-<version>-*/build-release.log`, the installer,
bootstrap ZIP, and `update-result.json`. Already-published runs skip rebuilding
and save only the update result. The standard publisher creates Git refs and
committed bootstrap downloads; it does not create a GitHub Releases page or
upload extra assets. That remains a separate capability.
