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
