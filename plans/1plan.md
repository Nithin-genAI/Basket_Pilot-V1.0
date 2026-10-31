# BasketPilot v1 — Execution Schedule

This schedule assumes a three-hour focused build window. It is deliberately ordered around an end-to-end mock flow, so the project has a usable demo even if platform access is unavailable.

## 0:00–0:15 — Repository reconnaissance and runnable baseline

- Read repository instructions and inspect all project files, package metadata, and existing entry points.
- Confirm the runtime and use existing conventions/dependencies.
- Record the actual starting state and one chosen run command.
- Do not spend this block implementing integrations or scaffolding a second framework.

**Checkpoint:** we know what runs, how it runs, and which parts already exist.

## 0:15–0:35 — Core contracts and mock data

- Define the core item, candidate offer, fee schedule, assignment, cart plan, preview, and session models.
- Define the `PlatformAdapter` interface from the PRD's capability list, limited to what the v1 flow needs.
- Add a fixture-backed `MockAdapter` with at least two mock platforms, enough items to show a split, fees, and one unavailable-item case.

**Checkpoint:** the search and cart layers can be exercised without credentials or network access.

## 0:35–1:05 — Deterministic optimizer

- Normalize quantities and comparable unit prices where fixture data supports it.
- Compare platform offers while respecting availability and quantity limits.
- Add delivery/handling/small-cart fees for each used platform and the configured multi-platform penalty.
- Compute best-single-platform and naive item-price baselines.
- Return assignments, totals, unfulfilled items, and short explainable reasons.

**Checkpoint:** a fixed fixture list produces a repeatable, fee-aware plan.

## 1:05–1:30 — Parser and list intake

- Implement a small deterministic parser for common forms (`2 x`, `1 kg`, `dozen`, comma/newline separated items).
- Define a typed parser boundary so ASI-1 can be plugged in later without coupling the optimizer to an LLM.
- If ASI-1 configuration is already available, wire only the documented endpoint/model and validate output against the same schema; otherwise retain deterministic parsing.
- Return ambiguity notices for items whose quantity/unit cannot be interpreted safely.

**Checkpoint:** an ordinary pasted list becomes structured items and parsing failure does not stop the mock demo.

## 1:30–2:00 — Orchestrated plan flow

- Add a session state machine for list intake, search, plan-ready, cart-building, preview-ready, awaiting-confirmation, and placed/failed.
- Fan out to adapters concurrently only if the existing runtime makes this straightforward; otherwise keep calls modular and sequential for v1.
- Render a concise plan with item, platform, line price, itemized fees, total, and best-single comparison.
- Let the user choose the proposed plan or request a single-platform plan if that is easy to support cleanly.

**Checkpoint:** one entry point can take the fixture list through a complete plan response.

## 2:00–2:30 — Cart, preview, confirmation safety

- Add assigned items to each selected adapter and verify the returned cart snapshot against expected quantities and lines.
- Build a preview from the observed mock cart snapshot; label estimates distinctly.
- Generate a short-lived token bound to platform, item IDs/SKUs, quantities, address identifier if used, and bill total.
- Require an explicit confirmation action that supplies the current token before invoking any placement method.
- Reject expired, mismatched, reused, or stale tokens. In mock mode, placement returns a clearly labeled simulated result.

**Checkpoint:** the demo can show a preview and prove that an unconfirmed or changed cart cannot be placed.

## 2:30–2:45 — Failure paths and recovery

- Exercise adapter unavailable/timeout, closed store, item out of stock, and cart mismatch using fixture switches or simple mock scenarios.
- Keep the remaining platform usable when one platform fails.
- Show actionable user-facing messages and avoid logging credentials or tokens.

**Checkpoint:** one platform failure degrades the plan rather than breaking the session.

## 2:45–3:00 — Runbook and demo handoff

- Write the mock-mode setup/run steps and the remaining live integration requirements.
- Add a short 3–5 minute demo script based only on implemented behavior.
- Manually run the documented flow from a fresh process and verify the visible output and confirmation gate.
- Update the PRD's status/checklist only for work actually completed.

**Checkpoint:** a new operator can run the mock demo and tell exactly which features are not live.

## Timebox cut lines

If time slips, defer in this order:

1. Rich parser cases and ASI-1 integration.
2. Concurrent fan-out and additional mock platforms.
3. Address cards, match carousels, and other interactive cards.
4. Persisted sessions and polished recovery for every failure class.
5. OAuth/MCP integration, unless already working at the start.

Do not cut: mock run path, deterministic landed-cost calculation, visible preview, explicit confirmation gate, and honest run instructions.

## End-of-timebox decision

At hour three, label the result either **demo-ready mock v1** or **not demo-ready**, based on the acceptance checklist in `plan.md`. Do not label it production-ready or claim real orders work unless the live integration and authorization flow were actually verified.
