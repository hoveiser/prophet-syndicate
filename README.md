# ProphetSyndicate

**AI rebalanced prediction market liquidity syndicate and senior / junior tranche capital router for the ProphetGrid ecosystem.**

Liquidity providers fund a single custody vault in two tranches. GenLayer validators read a live
macro implied volatility print and the depth of the linked ProphetGrid market book, quantize both
onto a discrete 250 bps grid, and must agree exactly on one of four routing bands and on every wei
number that band produces. The band decides how much syndicate liquidity is routed into ProphetGrid
market making, how much stays inside the senior protection shield, and the tranche yield rates for
that epoch. That routed capital is not only derived and stored: the syndicate moves it with a real
value carrying cross contract call into the live ProphetGrid, the grid records the syndicate address
as a position holder, and the position must be read back and reconciled before the epoch may settle.

| | |
|---|---|
| Deployed intelligent contract (studionet, chain 61999) | `0x0F9e290348e1500fA0800DDB89e5d770723038ef` |
| Deploy transaction | `0xcfe195a7674c02f3120fa4a56774c889875b4ac6abfe57dcac0257b2af9fc913` |
| Deployed commit | `970d08c` (tag `studionet-v2`), the exact source whose sha256 matches the deployed bytecode |
| Explorer | <https://genlayer-explorer.vercel.app/contracts/0x0F9e290348e1500fA0800DDB89e5d770723038ef> |
| Live desk | <https://hoveiser.github.io/ProphetSyndicate/> |
| Linked live ProphetGrid (real cross contract target) | `0x31C53838fF49168128D772625e34d3D2F7e11116` |
| Contract source | `contracts/prophet_syndicate.py`, 44,342 bytes, sha256 `49007b0c067ef84b9c82a9eec5a7bc672be2e7ed03c4896082f287561b66b70e`, pinned runner `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |
| Tests | 84 direct mode tests, `genvm-lint check` clean |
| Evidence | `evidence/studionet_deployment.json`, `docs/adversarial-review.md`, `docs/evidence.md` |

## What is actually on chain

The vault is funded with native GEN. Custody is accounted as assets against liabilities and the
contract proves the balance on chain. Assets are the native GEN still held by the account plus the
capital currently deployed inside ProphetGrid plus the proceeds already reconciled back from it.
Liabilities are the two tranche principals, the claimable bucket, the locked bonds and the yield
pool. The partition is exact at every step:

```json
"checks": {
  "custody_partition_exact": true,
  "pst_backed_by_custody": true,
  "locked_principal_reserved": true,
  "deployed_within_custody": true
}
```

`native_custody_balance` is decremented the instant capital really leaves for ProphetGrid, so it
always equals `eth_getBalance` of the contract account, while `active_deployed_to_prophet_grid` and
`reconciled_proceeds_total` carry the off-account value as recognized assets.

| Bucket | Meaning |
|---|---|
| `senior_principal_wei` | principal protected tranche, first claim on the yield pool |
| `junior_principal_wei` | high alpha market making sleeve, subordinate yield |
| `claimable_wei` | rejected value, released principal and settled yield, all claimable |
| `locked_bond_wei` | native routing bonds locked while an epoch is in flight |
| `yield_pool_wei` | the only source of yield: 25 bps entry fees plus audit bonds |

`get_custody_accounting_summary()` also reports the vault account balance read straight from the
chain node (`vm_account_balance_wei` in the view, `native_balance_of_vault_wei` in the evidence),
so the ledger is checkable against the account rather than taken on trust.

## The routing decision

Inside one `gl.nondet` leader function, storage is snapshotted into plain locals first, then two
live documents are fetched with `gl.nondet.web.render(url, mode="text")`:

* macro implied volatility, `https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX`
* linked ProphetGrid market depth, the ecosystem evidence file of the accepted ProphetGrid module

Both prints are quantized onto a 250 bps grid (`quantized_volatility_baseline_bps`,
`quantized_tail_risk_bps`) and the tail risk print selects one discrete band:

| Band | Tail risk corridor | Routed to ProphetGrid | Held in the senior shield |
|---|---|---|---|
| `HIGH_CONVICTION_DEPLOYMENT` | 0 to 2000 bps | 80 percent | 20 percent |
| `BALANCED_HEDGE_ALLOCATION` | 2250 to 5000 bps | 50 percent | 50 percent |
| `DEFENSIVE_COLLATERAL_BUFFER` | 5250 to 7500 bps | 20 percent | 80 percent |
| `CORRELATION_FREEZE_WITHDRAWAL` | above 7500 bps | 0 percent | 100 percent, penalty free redemption |

`gl.eq_principle.prompt_comparative` requires 100 percent agreement on all seven consensus fields
(`routing_band`, the two quantized prints, `derived_active_deployment_amount`,
`derived_senior_reserve_buffer`, `derived_senior_yield_bps`, `derived_junior_yield_bps`). After
consensus returns, the contract re-derives the whole allocation with integer arithmetic from the two
prints and reverts unless every field matches, so validators decide *what the market looks like*
and never get to invent a number that moves funds.

## Real cross contract deployment and reconciliation

The routed print is only meaningful if the capital actually moves and comes back reconciled, so
three mechanisms sit on top of the routing consensus:

* `deploy_routed_capital_to_prophet_grid(epoch_id, target_market_id, yes_leg_amount, no_leg_amount)`
  takes a `FINALIZED` epoch and sends the two legs with `grid.emit(value=..., on="finalized")
acquire_outcome_shares_payable(...)` to the linked ProphetGrid. It checks the legs sum to the routed
  print exactly, that each leg is inside the grid staking floor and ceiling, that the vault really
  holds the capital, then decrements `native_custody_balance`, raises
  `active_deployed_to_prophet_grid`, and stores a `MarketPosition`.
* `reconcile_prophet_grid_position(epoch_id)` reads the live grid book back through
  `grid.view().preview_payout(market, self)`. A finalized market with a payout claims the winnings;
  otherwise it falls back to the refundable principal. A gain is booked to the yield pool; a loss
  shaves junior principal first then senior. It clears `active_deployed`, raises
  `reconciled_proceeds_total` and marks the position `RECONCILED_LIVE`.
* `settle_tranche_epoch` is gated: an epoch whose routed capital is greater than zero will not settle
  until that capital has been deployed to and reconciled with ProphetGrid.

`get_epoch_prophet_grid_position(epoch_id)` and the `active_deployed_to_prophet_grid` and
`reconciled_proceeds_total` fields inside `get_custody_accounting_summary()` expose all of it for
independent verification.

## Steward rules

| Rule | Where it lives |
|---|---|
| 1 pinned runner, classic SDK | line 1 of the contract, `from genlayer import *`, `@allow_storage @dataclass` |
| 2 no backdoors, no manual clock | no admin rebalance path, every window compared against `gl.message_raw["datetime"]`, no `advance_clock` |
| 3 funded custody, maximum liability | `vault_custody_balance`, `senior_tranche_principal`, `junior_tranche_principal`, `bond_custody`, `@gl.evm.contract_interface` account proof, `reserved_liability_wei` |
| 4, 9 consensus baselines and bands | `_render_text`, `_volatility_bps`, `_depth_tail`, `_derive_routing_fields`, `ROUTING_PRINCIPLE`, four discrete bands |
| 5 one invariant helper | `_compute_syndicate_epoch_state` is the only source for phases, timers, NAV and reservation |
| 6 sanitization, temporal precedence | `_clean_text`, `_clean_url`, `_scan_number`, `_phase` gates every transition |
| 8 non trapping payables | all four payable entry points credit `refundable_balances`, record `last_payable_rejection`, and pay out through `withdraw_refund` / `claim_refundable_balance` which restore the credit if the native send fails |
| 10 enforceable timing, zero winner refunds | `execute_consensus_rebalance` needs `subscription_close_epoch`, `settle_tranche_epoch` needs `rebalance_challenge_deadline_epoch`, `REFUNDABLE_ZERO_UTILIZATION` and `rebalance_timeout_epoch` feed `claim_epoch_principal_refund` |
| 11 four way identity | `scripts/verify_identity.py`, result in `evidence/studionet_deployment.json` |

## Live scenario, every transaction chain checkable

48 transactions, all `FINALIZED` with leader execution `SUCCESS` and result `MAJORITY_AGREE`. Every
epoch that routed capital created its own live ProphetGrid market, deployed into it, and reconciled
before settling:

| Epoch | Route | Band | Routed | Live market | ProphetGrid status | Outcome |
|---|---|---|---|---|---|---|
| 1 | 6 GEN senior, 3 GEN junior | `HIGH_CONVICTION_DEPLOYMENT` | 7.182 GEN | 6 | `RECONCILED_LIVE` | real dual leg deploy, reconciled, settled |
| 2 | 4 GEN senior, 2 GEN junior, challenged then re audited | `HIGH_CONVICTION_DEPLOYMENT` | 4.788 GEN | 7 | `RECONCILED_LIVE` | deployed, reconciled, settled after a paid re audit |
| 3 | 2 GEN senior, 1 GEN junior, never audited | none | 0 | none | `UNDEPLOYED` | Rule 10 timeout, both LPs recovered 100 percent of principal, bond forfeited to the pool |
| 4 | 3 GEN junior, 2 GEN senior | `DEFENSIVE_COLLATERAL_BUFFER` | 0.9975 GEN | 8 | `RECONCILED_LIVE` | deployed, reconciled, settled |
| 5 | 6 GEN senior, 4 GEN junior | `HIGH_CONVICTION_DEPLOYMENT` | 7.98 GEN | 9 | `RECONCILED_LIVE` | dedicated cross contract round trip, deployed, reconciled, settled |

Cross contract proof (all `FINALIZED` / `SUCCESS`): epoch 5 created live market 9
(`0xb41eb9a137b831c03732405c081aba5630fb9e7732671ae3f654788db747b556`), deployed 3.99 / 3.99 GEN
dual leg (`0xfcf7ddc667439bc60ce2dee18a9e4e77fa6305c11dc4b176c135a94746c10c02`), then reconciled
against the grid's own book (`0x61fa7097e4e19feb97387a78a7b45711e93acb5c690ebeb598bfa40a6b2418f4`).
At that moment the linked ProphetGrid `get_trader_portfolio` listed the syndicate contract
`0x0F9e...38ef` as a real position holder across markets 6, 7, 8 and 9.

Soft fail proof: a proposal carrying only a quarter of the routing bond finalized as `SUCCESS`
without opening an epoch, the 2.995 GEN stayed claimable by that address, and `withdraw_refund` paid
it out in a separate finalized transaction (`0x9ea95eab63d0c8569b50...`). At the end of the run the
ledger `native_custody_balance` and `eth_getBalance` of the vault both read 1.12 GEN, so the claim
rail really moved native value and the deployed capital is accounted as an off account asset rather
than double counted.

Reproduce with:

```bash
.venv/bin/python scripts/deploy_studionet.py            # full scenario, resumable
.venv/bin/python scripts/deploy_studionet.py --status   # what has been recorded
```

## Repository map

```
contracts/prophet_syndicate.py       the intelligent contract (44,342 bytes)
tests/test_prophet_syndicate_direct.py  84 direct mode tests
tests/conftest.py                    fixtures, feed mocks and the fixed epoch clock
scripts/deploy_studionet.py          studionet deployment and live scenario with evidence
scripts/verify_identity.py           four way sha256 identity check
scripts/render_syndicate_emblem.py   the 512x512 emblem, drawn from the tranche waterfall
scripts/produce_syndicate_overview_video.py  52 second subtitled desk video with an ambient bed
scripts/publish_github.py            repository creation, main and gh-pages push
frontend/                            the quant tranche desk (Vite, React, TypeScript, Tailwind)
evidence/                            machine readable on chain proof (checkpoint is git ignored)
docs/                                emblem, demo video, evidence and review notes
```

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install genlayer-py genlayer-test genvm-linter python-dotenv web3 requests pillow numpy
.venv/bin/genvm-lint check contracts/prophet_syndicate.py
.venv/bin/pytest tests -q
cp .env.example .env          # GENLAYER_PRIVATE_KEY and GITHUB_TOKEN
```

Frontend:

```bash
cd frontend
npm install
npm run dev                            # http://127.0.0.1:5183/prophet-syndicate/
npm run build
```

The desk loads in a handful of chain reads: `get_custody_accounting_summary`,
`get_tranche_position`, one `get_epoch_prophet_grid_position` per routed epoch to render the live
ProphetGrid position and reconciliation ledger, `eth_getBalance` for the vault and, for LPs with a
key, one estimate per action. Burner desks for three independent LPs are generated in page storage,
and MetaMask or any injected wallet can sign the same calls, including deploy and reconcile.

## What is on chain, what is not

* The contract owns custody, the routing decision, the temporal gates and every payout.
* The frontend owns presentation, the local integer preview of the band model and the wallet.
* Feed providers own the raw numbers. Validators re-fetch both documents themselves, and the
  quantized grid plus the deterministic re-derivation is what keeps a drifting feed from moving
  funds. If a feed is unreachable the consensus call reverts, the epoch simply is not audited, and
  after `rebalance_timeout_epoch` LPs recover their full principal.
* `evidence/_checkpoint.json` is local run state and is git ignored on purpose.

## Known limits, stated plainly

* Tranche yield is funded only by entry fees and audit bonds, so the on chain prints show the
  senior floor target and the unfunded part of it (`unfunded_floor_wei`) rather than promising a
  rate the vault cannot pay.
* Epochs are sequential. One routing window at a time keeps capital attribution exact, at the cost
  of throughput.
* The `eth_getBalance` answer for externally owned accounts on studionet lagged during the run, so
  the evidence claims the vault account balance proof and the finalized payout transactions, not a
  per wallet delta.
* Studionet finality for a consensus call took 40 to 60 seconds per transaction in this run.
* The linked ProphetGrid redeems external positions into its own PGT collateral, not native GEN, so
  reconciled proceeds are recognized as an in contract asset (`reconciled_proceeds_total`) and the
  native custody balance is only restored when that collateral is later unwound. Deploy therefore
  really lowers the account balance and reconcile really raises the asset side, never both at once.
* The live round trips reconciled against markets still inside their open window, so proceeds were
  booked at par (refundable principal); realized gain and loss paths are exercised by the mocked
  cross contract tests, which drive a finalized payout and a below par payout deterministically.
* The linked ProphetGrid address is a deployer set trust assumption; it is fixed in the constructor
  and never writable afterwards, and the deploy, reconcile and probe all read the same one address.
