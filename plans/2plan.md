# BasketPilot v1 — Modular Technical Design

This design maps the PRD into a small vertical slice that can be implemented quickly. It preserves the option to add uAgents, ACP, ASI-1, and official MCP adapters later without making the three-hour mock demo depend on them.

## Module map

| Module | Responsibility | Depends on | Must not do |
|---|---|---|---|
| Entry point / chat handler | Accept a user message and emit progress/results | Orchestrator | Call platform MCP tools directly |
| Orchestrator | Route each session through parse, search, optimize, cart, preview, confirm | Core interfaces | Compute prices or bypass confirmation |
| Session store | Hold session state, plan, and current preview reference | Models | Persist OTPs, CVV, payment data, or raw auth tokens |
| Parser | Convert user text to typed requested items and ambiguity notes | Models; optional ASI-1 wrapper | Calculate totals or trigger checkout |
| Search service | Request candidates from each configured adapter | Adapter interface | Leak platform-specific response schemas |
| Platform adapter | Normalize platform operations, errors, fees, cart snapshots | Official MCP client or mock fixtures | Place an order except through guarded checkout service |
| Optimizer | Assign candidate offers and calculate landed totals/baselines | Models, normalized offers, fee schedules | Call an LLM or depend on network state |
| Checkout service | Build cart, validate snapshot, create preview, validate confirmation | Adapter, token signer/store | Trust a user-supplied token without binding checks |
| Renderer | Produce readable plan, fee breakdown, warnings, and preview | Models | Claim estimated fees are exact or mock orders are real |

## Core data contracts

Use the repository's native model/validation library if one exists. Keep these concepts stable even if field names evolve:

- **RequestedItem:** stable ID, display name, requested quantity, canonical unit if known, optional brand/constraints.
- **CandidateOffer:** platform, platform SKU, title/brand, pack quantity/unit, price, stock status, max quantity, match confidence.
- **FeeSchedule:** itemized delivery, handling, small-cart, late-night, tax/other fees, free threshold/minimum if known, and whether each value is observed or estimated.
- **Assignment:** requested item ID, selected platform/SKU, pack count, resolved quantity, line amount.
- **CartPlan:** assignments, per-platform subtotal and fees, landed grand total, baselines, savings, unfulfilled items, rationale.
- **CartSnapshot:** observed lines, quantities, prices, fees, and total returned by the adapter after cart construction.
- **CheckoutPreview:** snapshot, warnings, creation/expiry times, and an opaque server-side token reference.
- **Session:** state, parsed items, address reference if any, candidates, plan, previews, timestamps, and last error.

Represent money consistently (prefer integer paise or a decimal type; avoid binary floating-point comparisons). Store quantities with units so comparisons such as grams vs kilograms are explicit.

## Adapter boundary

The v1 adapter needs only the operations used in the working flow:

```text
capabilities() -> set of supported operations
ensure_auth() -> ready | needs_login | unavailable
resolve_items(items) -> normalized candidate offers + store status
add_to_cart(assignments) -> normalized cart snapshot + validation result
clear_cart() -> result (if supported)
place_order(guarded request) -> result (mock in v1; live only after confirmation)
```

For the first runnable slice, implement `MockAdapter` only. Its fixtures should support at least two platforms, differing prices and fees, an unavailable item, and a controllable failure. Implement live adapters only after reading and introspecting the official MCP schemas; record the observed tool names and limitations in project docs.

## Optimizer behavior

1. Discard unavailable offers and offers that cannot satisfy quantity/max-quantity constraints.
2. Normalize comparable units and calculate the required pack count and line total.
3. Evaluate each eligible platform subset (the initial scope has two mock platforms).
4. Assign each item to the lowest-cost eligible offer within each subset.
5. Add fees once per used platform and apply the configured extra-platform penalty.
6. Compare feasible totals, including minimum-order and free-delivery conditions if fixture data includes them.
7. Return the least-cost plan deterministically, plus best-single and naive-per-item baselines.
8. Mark items with no feasible offer as unfulfilled; never silently remove them.

Keep tie-breaking stable (platform name then SKU, or another documented deterministic key) so repeated runs explain the same plan.

## Session flow and state transitions

```text
IDLE -> COLLECTING_LIST -> SEARCHING -> PLAN_READY
PLAN_READY -> CART_BUILDING -> PREVIEW_READY -> AWAITING_CONFIRM
AWAITING_CONFIRM -> PLACED (only after valid explicit confirmation)
Any active state -> FAILED with a recoverable message when required data/tool calls fail
```

An ambiguous list remains in the collection/review stage until clarified. A changed plan or cart invalidates prior previews. Session persistence can be skipped in the first timebox if the entry process is stable; if added, store only non-secret workflow data and apply a TTL.

## Confirmation gate

The checkout service creates a cryptographically random, short-lived preview token and stores its binding server-side. Bind it to at least:

```text
session_id + platform + cart line identities/SKUs + quantities + address reference + itemized total
```

On confirmation, reload the current cart/preview and verify token existence, expiry, binding equality, and unused status. Consume the token once before starting placement, and use an idempotency key or an order-status check where supported. Any cart mutation, price drift, address change, or expired token requires a new preview. Do not put the secret token in logs or LLM prompts.

In mock mode, the placement result must say it is simulated. In live mode, a payment link or QR is not proof of payment; only a platform confirmation is.

## LLM and external-service boundaries

- Optional ASI-1 access lives behind one typed parser/reranker wrapper.
- Deterministic fallback parsing is always available for the v1 demonstration.
- The optimizer never imports or calls an LLM client.
- Product titles, store notices, and platform errors are untrusted data; do not treat them as instructions.
- Never accept OTPs, passwords, CVV, or card details through chat or include them in prompts, logs, or session state.
- Live MCP calls use bounded timeouts/retries and normalized error categories; do not invent tools or endpoints.

## Suggested implementation sequence

1. Inspect existing source and adapt this module map to the repository's stack.
2. Define shared contracts and static fixtures.
3. Implement the mock adapter and deterministic optimizer.
4. Wire list intake through plan rendering.
5. Add cart snapshot, preview token, and explicit confirmation.
6. Add concise failure recovery and operator instructions.
7. Add optional ASI-1, uAgents/ACP, and official MCP adapters as separate follow-on slices, each verified against current official docs/tool schemas.

## Out of scope for this v1 design

Production multi-user auth, hosted token vaulting, autonomous payment, UPI Reserve Pay, web scraping/browser automation, payment protocol monetization, and claims of production platform access.
