# Security Policy

## Supported versions

Security fixes are applied to the current 0.x development line. Older experimental stage artifacts should be treated as historical evidence rather than supported distributions.

## Reporting a vulnerability

Please report a suspected vulnerability privately to the repository maintainers using GitHub's private vulnerability reporting feature when available. Do not include secrets, access tokens, private repository contents, or customer data in a public issue.

A useful report includes the affected Agent DevTools version, operating system/Python version, reproduction steps, expected vs actual behavior, and whether the issue can modify project files, execute unintended commands, expose local data, or weaken verification/replay guarantees.

## Security boundary

Agent DevTools executes project-configured commands on the local machine. A repository's `agent-tools.json`, check policy, adapters, bootstrap artifacts, and project-native scripts are therefore part of the local trust boundary. Review untrusted project configuration before executing it.

Agent DevTools does not require a network service, API key, daemon, or external account. This reduces remote attack surface but does not make untrusted local project code safe to execute.
