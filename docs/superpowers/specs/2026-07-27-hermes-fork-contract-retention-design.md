# Hermes Fork Contract Retention Design

**Goal:** Restore approved fork behavior lost during the upstream sync while reducing future sync risk through focused owners and execution-path contract tests.

**Approved scope:** Alfredo approved restoring delegation presentation and cost, plugin-context fencing, gateway activity production, and delegate receipt validation. Quiet compaction, cron receipt prompting, desktop-only context/Observatory behavior, profile-multiplex routing changes, compression-estimator changes, and MCP default changes remain out of scope.

## Architecture

### 1. Delegation presentation contract

One async delegation record must carry the metadata needed by both live and terminal roster views:

- batch-level `header_profile` and `header_toolsets` when every task resolves to the same explicit value;
- child-level `profile`, `model`, `reasoning_effort`, `toolsets`, `queued_at`, `started_at`, `ended_at`, `duration_seconds`, `tool_count`, and `cost_usd` when available;
- single-task and batch records must use the same child metadata shape;
- missing fields remain optional so durable records created by previous versions still render.

`tools/delegate_tool.py` owns resolved task metadata and execution results. `tools/async_delegation.py` owns accepted, running, and terminal timestamps plus durable event persistence. `gateway/async_subagent_roster.py` remains a pure consumer and formatter.

Live cost reporting must honor the existing `cost_corrections` configuration. `agent/usage_pricing.py` owns config loading and corrected `CostResult` values. Runtime callers continue calling `estimate_usage_cost()` without bespoke correction logic; ambient correction loading happens inside that canonical boundary. Explicit correction arguments remain available for deterministic offline attribution.

### 2. Plugin-context integrity contract

Each `pre_llm_call` plugin context result must pass through `build_plugin_context_block()` before it enters `api_content`. Gateway turn notes are trusted gateway content and remain outside the plugin fence. Stored user content stays clean, while the API sidecar remains byte-stable across replay.

`agent/turn_context.py` owns hook-result normalization and fencing. `agent/memory_manager.py` keeps fence escaping and block construction.

### 3. Gateway activity contract

Tool execution must populate the optional fields already exposed by `AIAgent.get_activity_summary()`:

- current tool name, safe preview, and start time;
- bounded recent activity;
- last completed tool and duration;
- current todo snapshot through the existing todo store.

A focused `agent/activity.py` owns redaction, bounded state updates, and summary-safe labels. Tool executors call this owner at real sequential and concurrent execution boundaries. No Desktop Observatory, timeline, raw context, or desktop artifact behavior returns.

### 4. Delegate completion evidence contract

After each child finishes, delegate result aggregation determines whether a receipt is owed using `tools.agent_receipt.owes_receipt()`, validates the final summary with `validate_text()`, and stamps:

- `receipt_owed`
- `receipt_valid`
- `receipt_errors`

Validation is fail-open for infrastructure faults and loud in result metadata. No repair loop or extra model call is added. Cron receipt prompting remains excluded.

## Update-Resistance Strategy

1. Keep domain logic in focused modules instead of adding more inline behavior to `gateway/run.py`, `run_agent.py`, or `tools/delegate_tool.py`.
2. Test producer-to-consumer behavior. Renderer tests using manually seeded state are insufficient.
3. Add focused contract tests at real boundaries:
   - resolved task metadata through durable async completion and roster rows;
   - ambient cost config through `estimate_usage_cost()`;
   - raw plugin hook output through `build_turn_context()` into fenced `api_content`;
   - real tool execution through `get_activity_summary()`;
   - child summary/tool trace through receipt stamps.
4. Add `docs/fork-contracts.md` as the compact source inventory for future sync audits. It lists each retained contract, owning files, and canonical focused test commands. It does not duplicate implementation details.
5. Keep the post-update audit deterministic: hunk-survival ranking first, exact-file bounded lanes second.

## Data and Compatibility

- No database migration. Existing JSON task/event records accept additional optional fields.
- Durable records without child lifecycle metadata continue using existing elapsed fallbacks.
- No public model-tool schema expansion.
- No new environment variables or dependencies.
- `cost_corrections.enabled: false` preserves upstream pricing behavior.
- Receipt validation does not reject or rerun completed child work.

## Error Handling

- Missing or malformed lifecycle timestamps degrade to existing duration rendering.
- Cost config parse/read failures log and use corrections-disabled behavior.
- Activity preview generation and redaction failures return bounded safe labels, never raw tool arguments.
- Receipt parser faults stamp invalid/unverified metadata without losing the child summary.
- Plugin fencing treats empty blocks as no injection and neutralizes embedded fence markers through the existing helper.

## Verification

Each task follows strict RED then GREEN evidence. Final gate includes:

- canonical focused test files for delegation, roster, pricing, turn context, activity, and receipts;
- adjacent gateway and conversation-loop tests;
- Python compilation and `git diff --check`;
- clean exact-path review after each isolated commit;
- gateway restart followed by one real single-child and one real batch `delegate_task` smoke;
- Telegram proof that running and completed rows show model, reasoning, timing, tools, and corrected cost without leaking context or secrets.

## Explicit Non-Goals

- Restoring durable hygiene-compaction notices.
- Adding receipt instructions to cron prompts.
- Restoring Desktop Observatory, context inspector, workstreams, raw context RPC, or subagent context artifacts.
- Changing profile-multiplex session routing.
- Changing compression recovery estimation.
- Changing Alfredo's MCP inheritance configuration or upstream default.
