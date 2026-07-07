# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems. Use
GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, or email **jeff.murray@alumni.upenn.edu** with:

- A description of the issue and steps to reproduce (minimum working example)
- The archgraph version and Python version
- Your proposed fix or mitigation, if any

You'll get an acknowledgement within 72 hours.

## Scope

archgraph is a local, read-only file parser. It does not run a server, store
credentials, or make network calls on its default path. The security surface
is small but worth noting:

- **Parsing untrusted files.** Parsers read YAML, JSON, and Python source
  (the Python-based parsers use `ast.parse` and **never execute** the file).
  A crafted input that causes a crash or resource exhaustion is in scope.
- **The opt-in LLM fallback.** `find_edges(..., llm_fallback=True)` sends file
  contents to a third-party LLM (Anthropic or Google). This is **off by
  default** precisely because it egresses data; anything that triggers egress
  without the caller opting in would be a serious bug.

## Supported versions

Only the latest minor version on PyPI receives fixes.
