# Hermes Fork Contracts

This fork carries a small set of runtime contracts that must survive upstream synchronization. Treat each contract as an execution-path requirement, not a renderer snapshot or local preference.

## Active Contracts

### Delegation lifecycle and cost

**Behavior**

- Foreground roster start events retain child model and reasoning metadata.
- Foreground completion events retain duration and tool count.
- Background dispatch records retain normalized child descriptors, shared profile/toolset headers, and queued timestamps.
- Child execution records started and ended timestamps at their real execution boundaries.
- Terminal child records retain status, duration, tool count, model, cost, and available profile/reasoning/toolset metadata.
- Single-child and batch events expose the same child shape.
- Durable records created without optional fields remain readable.
- Ambient cost corrections from `config.yaml` apply when callers do not provide an explicit correction policy.
- Explicit correction policies remain deterministic; disabled or unreadable ambient policy leaves base pricing unchanged.

**Owners**

- `gateway/run.py`
- `gateway/subagent_roster.py`
- `gateway/async_subagent_roster.py`
- `tools/async_delegation.py`
- `tools/delegate_tool.py`
- `agent/usage_pricing.py`

**Contract tests**

- `tests/gateway/test_subagent_roster.py`
- `tests/gateway/test_async_subagent_roster.py`
- `tests/tools/test_async_delegation.py`
- `tests/tools/test_delegate.py`
- `tests/agent/test_usage_pricing.py`

### Plugin context boundary

**Behavior**

- `pre_llm_call` plugin text is untrusted sidecar context.
- Plugin text is fenced with the canonical `<plugin-context>` builder before API submission.
- Embedded closing markers are neutralized by the canonical builder.
- Stored user content remains byte-identical.
- Trusted gateway notes remain outside and after the plugin fence.
- No plugin text mutates the system prompt or historical message content.

**Owners**

- `agent/turn_context.py`
- `agent/memory_manager.py`

**Contract tests**

- `tests/agent/test_memory_provider.py`

### Delegate receipt evidence

**Behavior**

- Every completed delegate result states whether a receipt is owed.
- Read-only lookup work does not owe a receipt.
- Write-capable delegated work validates the child summary with `tools.agent_receipt`.
- Valid receipts expose `receipt_valid: true`.
- Missing or invalid receipts expose `receipt_valid: false` with at most five bounded errors.
- Validator infrastructure faults do not discard child output or trigger repair calls. The receipt validator owns durable degraded-state signaling.
- Receipt validation never adds a provider/model call.

**Owners**

- `tools/delegate_tool.py`
- `tools/agent_receipt.py`

**Contract tests**

- `tests/tools/test_delegate.py`
- `tests/tools/test_agent_receipt.py`

### Runtime activity production

**Behavior**

- Sequential and concurrent tool executors publish activity at real start and completion boundaries.
- Live state contains tool name, bounded safe preview, and elapsed time.
- Completion state contains tool name, duration, and error state.
- Recent tool history is bounded to three entries.
- Concurrent completion cannot erase another active tool.
- Credential-shaped values are redacted before assignment to activity state.
- Terminal activity uses a conservative generic command preview.
- Turn start clears stale active state.
- Summary rendering reads producer state without mutating it and degrades safely for minimal agents.

**Owners**

- `agent/activity.py`
- `agent/tool_executor.py`
- `run_agent.py`
- `gateway/heartbeat_status.py`

**Contract tests**

- `tests/run_agent/test_activity_producer_visibility.py`
- `tests/run_agent/test_activity_summary_visibility.py`
- `tests/run_agent/test_tool_executor_contextvar_propagation.py`
- `tests/gateway/test_heartbeat_status.py`

## Retained Fork Behavior

The following contracts remain active and should stay covered by their existing focused tests:

- Todo timing and hydration.
- Delegation provenance and verify-status child prompt contract.
- Per-agent tool-call and output budgets.
- Per-job reasoning selection.
- Cron wall-clock timeout.
- Telegram topic healing, routing, and administration.
- Kanban receipt gate.
- Goal judgment.
- Verification-evidence paths.

## Intentional Exclusions

Do not restore these without a separate approved requirement and execution-path proof:

- Cron receipt prompt guidance.
- Durable compaction notices; quiet in-place compression is intentional.
- Desktop Observatory/context-inspector and raw context RPC artifacts.
- Profile-aware gateway routing/session-key behavior without a demonstrated multi-profile gateway requirement.
- Compression-estimator precision changes without a reproducible schema-heavy overflow.
- Inherited MCP toolsets as an implicit default while live configuration explicitly disables inheritance.

## Upstream Synchronization Gate

Before accepting an upstream synchronization:

1. Diff every active contract owner against the fork base and classify changed call paths.
2. Run the focused contract suites below before resolving behavior as retained.
3. Require producer-to-consumer evidence. Renderer-only tests and manually seeded state do not prove producer wiring.
4. Update this manifest in the same commit when a contract owner or intentional exclusion changes.
5. Record any removed contract as an explicit product decision; never infer removal from a green broad suite.

```bash
uv run --extra dev pytest \
  tests/gateway/test_subagent_roster.py \
  tests/gateway/test_async_subagent_roster.py \
  tests/gateway/test_display_config.py \
  tests/tools/test_async_delegation.py \
  tests/tools/test_delegate.py \
  tests/tools/test_agent_receipt.py \
  tests/agent/test_usage_pricing.py \
  tests/agent/test_memory_provider.py \
  tests/run_agent/test_activity_producer_visibility.py \
  tests/run_agent/test_activity_summary_visibility.py \
  tests/run_agent/test_tool_executor_contextvar_propagation.py \
  tests/run_agent/test_tool_batch_segmentation.py \
  tests/gateway/test_heartbeat_status.py \
  -q -o 'addopts='
```

Run the same files through `scripts/run_tests.sh` before publication. A missing async test dependency, skipped execution-path test, or renderer-only pass is not a green contract gate.
