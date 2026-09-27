# Public onboarding contract

The public bootstrap should optimize for the user's goal, not for knowledge of Agent DevTools internals.

## Empty workspace

The first installation step is always the minimal universal non-development layer. It must not invent a language, framework, package manager, test runner, build system, or deployment model.

After that layer exists, interactive onboarding asks one human question: what is planned in this workspace? Non-interactive onboarding returns `needs-intent` with the same required question.

If the answer is development, stack details are derived from the answer where possible and only missing information is requested. Specialization must preserve already recorded requirements, findings and durable knowledge.

## Existing workspace

Evidence comes before questions. The bootstrap inspects repository metadata and source signals first. If the stack is already visible, the user must not be asked to repeat it.

A supported stack may map to a bundled preset. A recognized but unsupported stack remains explicit: the bootstrap reports the detected technologies and requests only missing project-native verification/adapter decisions.

## Internal profiles

`general`, `research`, `analysis`, `document`, and `development` are implementation-level workflow profiles. Normal users should describe the work they want to do rather than choose an internal profile by name.

Explicit profile overrides remain available for automation, diagnostics, and advanced use.
