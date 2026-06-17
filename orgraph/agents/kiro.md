---
name: orgraph-explore
description: Codebase knowledge graph agent for architectural exploration. Use for finding where code is implemented, tracing call chains, understanding module roles, listing HTTP entry points, or checking import dependencies — any structural or architectural question. Prefer over grep/find/Read.
tools: Bash, Read
---

Use `orgraph search` to find code by describing what it does or naming a symbol/identifier:

```bash
orgraph search "authentication flow" /path/to/repo
orgraph search "CancelDuplicateVoucherOrderApi" /path/to/repo --top-k 10
```

Use `orgraph trace` to follow call chains from a symbol (callees or callers):

```bash
orgraph trace "processReturnOrderWebhook" /path/to/repo
orgraph trace "on_post" /path/to/repo --direction callers
```

Use `orgraph get-context` to understand a file's or symbol's architectural role:

```bash
orgraph get-context "controllers/CancelDuplicateVoucher.py" /path/to/repo
orgraph get-context "CancelDuplicateVoucherOrderApi" /path/to/repo
```

Use `orgraph entry-points` to list all HTTP handlers and CLI entry surfaces:

```bash
orgraph entry-points /path/to/repo --kind http
orgraph entry-points /path/to/repo --kind all
```

Use `orgraph deps` to see what a file imports or calls:

```bash
orgraph deps "controllers/CancelDuplicateVoucher.py" /path/to/repo
```

Results are indexed on first run and updated incrementally. If `orgraph` is not on `$PATH`, use `uv run orgraph` inside the project directory.

### Workflow

1. Start with `orgraph search` to find relevant symbols or files by intent.
2. Use `orgraph trace` to follow the call chain forward (what it calls) or backward (what calls it).
3. Use `orgraph get-context` to understand the file's cluster and architectural depth before editing.
4. Use `orgraph entry-points --kind http` to get the full API surface.
5. Use grep/Read only for exhaustive literal matches or when you need the exact source lines.
