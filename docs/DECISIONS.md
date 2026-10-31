# Decisions

## 2026-10-31 — Optimizer model extensions

Step 2 reuses the existing `ParsedItem`, `Candidate`, `ItemCandidates`, `SearchResult`, `FeeSchedule`, `Assignment`, and `CartPlan` contracts. The following backward-compatible fields or type clarifications were added in `basketpilot/models.py`:

- `FeeSchedule.small_cart_threshold` identifies when its existing `small_cart` fee applies.
- `FeeSchedule.fees_estimated` marks a supplied fee schedule whose amounts are estimates.
- The five fee amount fields on `FeeSchedule` are now optional at the boundary (rather than defaulting to zero); an omitted fee is treated as zero for computation and marks the plan estimated. Explicit zero means a known zero fee.
- `Assignment.overbuy_qty` and `Assignment.overbuy_unit` report package overbuy.
- Optimizer-produced `Assignment.unit_price` and `line_total`, `CartPlan.per_platform`, `grand_total`, `baselines`, and `savings_vs_best_single` use integer paise. Step 1 model declarations used floats for monetary amounts; optimizer inputs (`Candidate.price` and `FeeSchedule` amounts) remain rupees at the boundary and are converted immediately with decimal half-up rounding.
- `CartPlan.savings_vs_naive`, `fees_estimated`, `suggestions`, and `objective_paise` expose required Step 2 results. `grand_total` is the landed amount; `objective_paise` additionally includes the multi-platform penalty.

No existing model or Step 1 test was renamed or removed. `FeeSchedule.min_order` is the existing contract for the requested minimum-order value. If a fee schedule is absent, the optimizer assumes zero fee amounts, marks the result as estimated, and documents that limitation in the plan.

## 2026-10-31 — Optimizer ambiguities

- No catalog of unrequested filler products is present in `SearchResult`. The optimizer therefore never invents/adds a product; when it can determine a threshold gap, it suggests an eligible extra item generically with the required rupee amount.
- `Candidate.max_qty` is treated as a maximum number of packs. An item that cannot be fully assigned due to stock/quantity or a platform minimum order is reported unfulfilled with a reason.
- A missing `FeeSchedule` has unknown fees and no minimum-order constraint; it is estimated as zero so the plan remains usable, with `fees_estimated=True`.
- Comparability uses mass, volume, and count dimensions. If either the requested quantity or pack-size string is unknown or dimensionally incompatible, requested quantity is interpreted as packs and overbuy is measured in packs.
- Baseline monetary values are integer paise. If no feasible single-platform plan exists, `best_single_platform` is `None` and `savings_vs_best_single` is zero.
- For fee thresholds, free delivery applies at or above its threshold; the small-cart fee applies strictly below `small_cart_threshold`. `min_order` is a hard feasibility floor on item subtotal.

## 2026-10-31 — Step 2 baseline and fixture corrections

- `price_floor` is the fee-free sum of each fulfillable item's cheapest line amount. It is an informational lower bound and may not be feasible after platform fees/minimum-order rules.
- `naive_with_fees` assigns each fulfillable item to its cheapest platform, applies actual known platform fees and the configured extra-platform objective penalty. It is `None` when that naive assignment violates a platform minimum order. `savings_vs_naive` is clamped at zero and compares objective values.
- Parser duplicate identity is normalized (Unicode NFKC, case-folded, collapsed whitespace) name + unit + brand. Matching duplicate lines sum quantities. Matching name/brand with differing units remains separate and adds one mixed-unit ambiguity note.
- The deterministic parser uses conservative trailing plural normalization (for example, `apples` and `apple`) for duplicate identity. It uses an existing `ParsedItem.brand` value in the identity but does not infer a brand from free text.
- The mock catalogs contain over 30 SKUs across two platforms. Because the catalog schema has no unrequested filler items, suggestions continue to name the amount needed generically; the requested 20-item demo includes the intentionally unstocked item and yields a Zepto subtotal Rs 30 below its free-delivery threshold.

## 2026-10-31 — Zepto Step 3A schema limits

- There is no `README.md` in the workspace. The relevant architecture and security contract is in `basket_project.md`; Sections 5.4, 6, 7, 11, and 12 were used.
- `docs/zepto_tools.json` lists 23 tools. Only search/detail responses have detailed output schemas; operational outputs including `view_cart`, address listing, serviceability, and payment methods have `outputSchema: null`.
- Search prices and MRP are explicitly paise; `Candidate` uses rupees at the adapter boundary and optimizer math converts back to integer paise. `availableQuantity` is stock quantity and is used as `max_qty`; missing availability means the returned search item is provisionally in stock. Search results do not declare brand or ETA, so those values are not inferred from product text and ETA remains unknown.
- The `search_multiple_products` input schema has no maximum query count, so the adapter sends one batched call per list. `zepto_shop` is annotated non-read-only and described as a widget entry point; it is classified READ only for `search`/`browse` intent and is not used for catalog resolution.
- Cart tool descriptions promise item identifiers and quantities, but the captured `view_cart` schema does not define a response container, bill amount, or fee fields. The adapter validates only the documented direct-list shape; it marks totals unverified, estimates fees, and blocks preview/order unless a real response establishes the bill fields. This is a deliberate fail-closed limit, not a guessed field mapping.
- A read-only fixture capture was run through the official MCP on 2026-10-31. Address listing returned prose containing a full address and address ID; the sanitizer now retains only its pincode in the checked fixture. Search and payment methods reported that no store was selected; `view_cart` confirmed an empty cart. No product detail, serviceability, or non-empty-cart response was available without selecting an address or supplying coordinates.
- The fixture recorder stores raw responses under ignored `docs/raw/` and sanitized versions under `tests/fixtures/zepto/`. It only calls reads by default. Address selection requires `--select-saved-address` and a literal `y`; cart changes require `--include-cart-write` and a literal `y` before each update. No write was made during implementation.
- Live `view_cart` returned only an empty-cart prose message. The available tool description promises item IDs and quantities but exposes no bill or fee fields, so cart validation, verified previews, and live order placement stay fail-closed until a user-approved recording captures the actual response shape.

## 2026-10-31 — Step 3B local uAgents and ACP

- The requested `README.md` is not present; `basket_project.md` remains the project specification and submission README. Sections 4, 5, and 9 are the source for this step.
- Pinned `uagents==0.25.5` and its compatible `uagents-core==0.4.12`; verified `AgentChatProtocol 0.3.0` from the installed official protocol package.
- The existing Step 1/2 domain dataclasses in `basketpilot/models.py` were not changed. `basketpilot/messages.py` adds uAgents `Model` wire types and narrow conversion helpers so existing `ParsedItem`, `Candidate`, `FeeSchedule`, `SearchResult`, `Assignment`, and `CartPlan` remain the business models. Wire monetary assignment totals are integer paise; catalog prices and fee schedules retain their existing rupee input boundary.
- Added no-op Bureau registration policy and left `BP_MAILBOX=false`. This keeps all agents local and avoids Agentverse registration. The terminal client joins the same Bureau in `scripts/chat_client.py`; it does not try to resolve or contact an external agent.
- Agent seeds are required environment variables (`BP_SEED_*`) and never generated or persisted by the runtime. `.env.example` contains placeholders only; `.env` is ignored by Git.
- `BP_USE_MOCK=true` is the demo default. `BP_USE_MOCK=false` creates the existing Zepto search adapter only; a real address must already be selected or supplied through `ZEPTO_ADDRESS_ID`. Live cart writes additionally require `BP_ALLOW_CART_WRITES=true`, and this Step 3B runtime never places orders even when `BP_ALLOW_ORDERS=true`.
- Session identity is a stable hash of the ACP sender because the current ACP `ChatMessage` has no session id field. SQLite stores state and parsed grocery items for 60 minutes; raw addresses and preview tokens are kept out of persistence. Preview tokens remain in checkout-agent memory for five minutes and confirmation returns a dry-run result by default.
- Ambiguities are handled as one batch: the parser asks for the full corrected list, then the workflow continues to the address prompt. The local mock asks for a label but deliberately does not store address text. Cart totals and optimizer math remain in integer paise internally; conversion to rupees occurs only at adapter/UI boundaries.
- Deterministic `llm.py` implementations are temporary fallbacks. No LLM SDK or ASI-1 call is included in Step 3B; ASI-1 remains Step 3C.
