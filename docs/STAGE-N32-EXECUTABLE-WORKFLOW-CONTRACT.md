# Stage N3.2 — Executable Workflow Contract

Dogfood in 0.8.8 exposed contract drift: `workflow show` / capabilities could advertise a command absent from the actual CLI.

N3.2 adds `workflow validate`, which derives the command inventory from the real argparse parser and validates every workflow-phase command against it. A missing advertised command fails closed.

`capabilities --json` now also exposes `cliCommands`, derived from the parser. The existing `commands` object remains the profile/feature capability map.

No second manually maintained command registry is introduced.
