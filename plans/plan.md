# BasketPilot v1 — Three-Hour Build Plan

**Source of truth:** [`../basket_project.md`](../basket_project.md)  
**Version:** 1.0  
**Timebox:** three focused hours  
**Goal:** deliver a small, runnable, demonstrable BasketPilot flow with modular boundaries and a safe order-confirmation gate.

## Product in one sentence

BasketPilot turns a grocery list into a fee-aware cart plan across available quick-commerce platforms, shows the bill, and requires explicit user confirmation before any order-placement call.

## What “done” means for this timebox

The v1 slice is complete when a user can:

1. Submit a grocery list in chat or the available local entry point.
2. See it parsed into named items, quantities, and units (with a deterministic fallback).
3. Get candidate offers from a mock platform adapter, with a live adapter used only if access is already configured and verified.
4. See a deterministic plan with item costs, platform fees, total, and best-single-platform comparison.
5. Build and validate a mock cart, then review an itemized preview.
6. Confirm the preview explicitly; the system must reject missing, expired, or stale preview tokens.

The project must start in mock mode without platform credentials. Each module should be replaceable without changing orchestration or optimizer logic.

## Three-hour scope

### Must build

- A runnable entry point and configuration with mock mode as the safe default.
- Typed core models for requested items, offers, fee schedules, assignments, plans, previews, and session state.
- A deterministic parser fallback for ordinary lists; call ASI-1 only if credentials and a known working endpoint are already available.
- A `PlatformAdapter` boundary and one fixture-backed `MockAdapter`.
- Fee-aware deterministic optimization and transparent plan rendering.
- A small conversation/session flow: list → plan → cart → preview → explicit confirm.
- A preview token bound to the cart details and bill; reject stale or invalid tokens before checkout.
- Concise setup/run instructions and a short manual demo script.

### Defer unless the core slice is complete early

- Real Zepto/Instamart MCP connections and OAuth, unless credentials/access are already working.
- Agentverse registration, mailbox connectivity, and ASI:One discovery.
- Multiple separately hosted worker agents; preserve the model/message boundaries, but run locally in one process if necessary.
- Interactive cards, browser automation, Payment Protocol, QR rendering, ILP/CP-SAT, multi-user support, and real payment.

Never simulate a successful live order. In v1, mock checkout is explicitly labeled; for a live adapter, do not call platform checkout without a valid preview and explicit confirmation.

## Architecture at a glance

```text
Chat/local entry
      |
Orchestrator + session state machine
      |------ Parser (ASI-1 optional; deterministic fallback)
      |------ Search service ------ PlatformAdapter (Mock by default; live adapters later)
      |------ Deterministic optimizer
      |------ Checkout service: cart -> preview -> confirmation gate
      |
Text response / itemized preview
```

Keep platform-specific schemas and errors inside adapters. Keep language interpretation inside the parser/ASI-1 wrapper. Keep all arithmetic inside the optimizer and fee modules. Keep secrets out of chat, logs, prompts, and persisted session data.

## Delivery order

1. Inspect the repository and identify the actual runtime, existing code, and available dependencies.
2. Create the smallest runnable vertical slice, using the repository's existing language and conventions.
3. Add models and a mock adapter with a handful of representative fixture offers and fees.
4. Implement and exercise deterministic fee-aware planning and readable output.
5. Connect the list-to-preview flow through a small session state machine.
6. Add the confirmation-token guard and demonstrate rejection of an unconfirmed/stale preview.
7. Write run instructions, known limitations, and the demo script; make one final manual pass.

## Acceptance checklist

- [ ] Runs locally in mock mode without external credentials.
- [ ] Same inputs produce the same plan.
- [ ] Fees are included in the landed total; per-platform line items are visible.
- [ ] Best-single-platform baseline is computed for comparison.
- [ ] Unavailable items are surfaced rather than silently dropped.
- [ ] Platform failure can be represented as unavailable without crashing the whole flow.
- [ ] Preview reflects the cart snapshot and clearly identifies estimated versus observed fees.
- [ ] No order-placement path is reachable without explicit confirmation and a valid preview token.
- [ ] Documentation states which parts are mock, live, and deferred.

## Risks and assumptions

- The supplied workspace currently contains the PRD but no application source files. The first implementation step must inspect what is present before selecting a framework or assuming a package layout.
- The PRD documents official integrations but explicitly says their current tools/schemas must be introspected. Do not invent tool names or claim an integration is live without verifying it.
- A three-hour deadline cannot responsibly promise the entire hackathon scope. The mock-backed core and safety gate are the deliverable; external account setup is an access-dependent follow-up.
- If actual project files are added or located elsewhere, preserve their established structure rather than replacing it with the PRD's proposed layout wholesale.

## Related plans

- [`1plan.md`](1plan.md): timeboxed execution sequence and checkpoints.
- [`2plan.md`](2plan.md): module responsibilities, interfaces, and safety boundaries.
