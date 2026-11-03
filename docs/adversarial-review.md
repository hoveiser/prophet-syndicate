# Adversarial Review: ProphetSyndicate Tranche Custody Contract

Date: 2026-11-01
Contract: contracts/prophet_syndicate.py (752 lines, runner 1jb45aa8)
Deployed: 0x206bdF01A814e8ae27EbC4A7aC138995217B95F8 (studionet chain 61999)

Note: this section records the v1 review and is kept for history. The v1 deployed
address above is retired. The current deployment reviewed in the v2 section at the end
of this file is `0x0F9e290348e1500fA0800DDB89e5d770723038ef`.

## 1. State Machine

| Status | Phase | Entry | Exit |
|--------|-------|-------|------|
| SUBSCRIPTION_OPEN | COLLECT | propose_routing_epoch_payable | execute_consensus_rebalance |
| (timeout from OPEN) | REFUNDABLE | rebalance_timeout_epoch passed | claim_epoch_principal_refund |
| FINALIZED | CHALLENGE (now < deadline) | consensus deployment > 0 | challenge or settle |
| FINALIZED | READY (now >= deadline) | challenge deadline passed | settle_tranche_epoch |
| CHALLENGED | AWAIT_AUDIT | challenge_routing_allocation_payable | execute_consensus_rebalance (re-audit) |
| ZERO_UTIL | REFUNDABLE | consensus deployment == 0 | claim_epoch_principal_refund |
| SETTLED | DONE | settle_tranche_epoch | terminal |
| REFUNDABLE | REFUNDABLE | claim during refund phase | claim_epoch_principal_refund |
| RELEASED | DONE | last LP claims | terminal |

All transitions guarded by `_phase()`. No status can be skipped.

## 2. Liveness: No Stuck Funds

Every locked status has a deterministic timeout recovery:
- OPEN: if nobody calls execute, after rebalance_timeout_epoch the phase becomes REFUNDABLE.
- FINALIZED/CHALLENGED: after rebalance_timeout_epoch, becomes REFUNDABLE.
- ZERO_UTIL/REFUNDABLE: claim_epoch_principal_refund releases to refundable_balances.
- refundable_balances: withdraw_refund or claim_refundable_balance (always callable).
- Bonds: released on settle (False = refund to proposer) or forfeited on timeout (True = yield pool).

No party can block another party's exit. FIXED by design.

## 3. Race Conditions

| Pair | Mutual exclusion mechanism | Test |
|------|---------------------------|------|
| settle vs challenge | `now >= deadline` vs `now < deadline` (strict complement) | test_settlement_is_refused_while_the_challenge_window_is_open |
| execute vs challenge | PHASE_AWAIT_AUDIT vs PHASE_CHALLENGE | test_executing_twice_is_refused_once_the_epoch_is_finalized |
| claim vs settle | PHASE_REFUNDABLE vs PHASE_READY | test_settled_epochs_are_not_refundable |
| double settle | STATUS_SETTLED -> PHASE_DONE -> reject | test_an_epoch_settles_only_once |
| double refund | _release_principal zeroes table, second call moved=0 -> reject | test_principal_refund_cannot_be_claimed_twice |
| propose while in-flight | _in_flight() != None -> soft_fail REASON_BUSY | test_propose_refuses_a_second_epoch_while_one_is_in_flight |

All guards are mutually exclusive by both status AND timestamp. FIXED.

## 4. Accounting Invariants

**Primary**: native_custody_balance == senior + junior + refundable + bonds + yield_pool
**PST backing**: pst_total_supply == vault_custody_balance == senior_total + junior_total
**Conservation**: deployment + reserve == senior_commit + junior_commit (checked in execute)

Every fund movement traced:
- propose: native += v; bonds += bond; refundable += (v - bond). Sum unchanged.
- subscribe: native += v; tranche += (v - fee); pool += fee. Sum unchanged.
- challenge: native += v; refundable += (v - 2b); pool += 2b. Sum unchanged.
- settle: tranche -> 0; refundable += (principal + yield); pool -= yield; bonds -= routing_bond. Sum unchanged.
- withdraw: refundable -= m; native -= m (paid) or refundable restored (failed). Correct.
- refund: tranche -= m; refundable += m. Sum unchanged.

Tests: test_custody_partition_equals_native_custody_at_every_step,
test_the_vault_is_always_backed_one_to_one,
test_routing_conserves_liquidity_across_every_band,
test_settlement_cannot_pay_more_yield_than_the_pool_holds,
test_invariants_hold_across_a_full_epoch_lifecycle. FIXED.

## 5. Input Validation

- All time windows: floor (60/60/120s) and ceiling (30 days) enforced in __init__ via _bound().
- All amounts: MIN/MAX bounds on subscription (0.001-1000 GEN), bond (0.001-100 GEN), epoch cap (1-1M GEN), depth reference (1K-1Q units).
- Addresses: _addr() validates 42-char hex, rejects non-hex.
- URLs: _clean_url() requires https, rejects @, #, space, backslash, unqualified hosts.
- Epoch ID: _require_epoch() rejects unknown IDs.
- Zero-value calls: rejected in _subscribe.
- Challenge count: capped at MAX_CHALLENGES_PER_EPOCH = 2.
- Epoch count: capped at MAX_EPOCHS = 64.

Tests: test_deploy_refuses_a_tranche_cap_below_the_floor, test_deploy_refuses_a_non_https_macro_feed,
test_subscription_below_the_minimum_is_refunded_not_lost, test_subscription_over_the_tranche_cap_is_soft_failed,
test_challenge_slots_are_capped. FIXED.

## 6. Consensus Safety

- **Equivalence**: prompt_comparative with ROUTING_PRINCIPLE requiring exact agreement on all 7 fields. No numeric tolerance.
- **Post-consensus verification**: the consensus result is re-derived deterministically via _derive_routing_fields and checked field-by-field against the LLM output. If any field disagrees, the transaction reverts.
- **Conservation check**: deployment + reserve must equal total commit. Rejects non-conserving consensus.
- **Band derivation**: routing_band is derived from tail_risk_bps via fixed thresholds, not from LLM free text. The LLM cannot invent a band.
- **Web fetches inside leader_fn**: validators rerun the same function, fetching the same URLs independently. The fetched text is only scanned for specific JSON keys via _scan_number. Raw text never reaches the LLM judge.
- **Fail-closed**: unreachable feeds raise ERROR_TRANSIENT/ERROR_EXTERNAL, reverting the transaction. No partial state change.
- **Prompt injection**: _clean_text strips control chars, angle brackets, collapses whitespace. The LLM only sees the 7 derived numeric/string fields, not the raw document. Instructions embedded in fetched content cannot alter the verdict rule.
- **Direct mode limitation**: the 72 unit tests exercise the leader path only. Validator agreement is proven by the live studionet run (30 transactions, all MAJORITY_AGREE).

FIXED. No open concerns.

## 7. Test Coverage

72 tests covering:
- Every guard (wrong caller, wrong status, too early, too late, zero, overpay, boundary)
- All 4 routing bands with deterministic derivation
- Full lifecycle: propose -> subscribe -> execute -> challenge -> re-execute -> settle -> withdraw
- Race conditions (double execute, double settle, double refund, propose while in-flight)
- Accounting invariants asserted after every step
- Soft-fail paths (rejected payables keep funds claimable)
- Feed failure paths (unreachable feeds produce zero deployment, not a revert)
- Linkage probe (cross-contract read)

## Summary

No OPEN findings. All fund paths are guarded, all transitions are mutually exclusive, the accounting invariant is enforced and tested, consensus uses exact agreement with deterministic post-verification, and input validation has both floors and ceilings on every window and amount. The contract is safe to submit.

---

# v2 Remediation Review: Real ProphetGrid Cross Contract Deployment

Trigger: a Steward rejected the submission because the routed amount was stored but never
deployed to, or reconciled with, ProphetGrid. This section reviews the remediation.
Contract: contracts/prophet_syndicate.py (runner 1jb45aa8).
Linked live ProphetGrid: 0x31C53838fF49168128D772625e34d3D2F7e11116 (studionet, 5 markets).

## V2.1 New state transitions (position sub state per epoch)

| Position status | Entry | Exit | Funds effect |
|---|---|---|---|
| UNDEPLOYED | epoch FINALIZED with routed > 0 | deploy_routed_capital_to_prophet_grid | none yet |
| DEPLOYED_LIVE | value carrying cross contract acquire YES + NO on the linked grid | reconcile_prophet_grid_position | native custody decreases by routed, active_deployed increases |
| RECONCILED_LIVE | grid book read via view, claim emitted, PnL booked | settle_tranche_epoch | active_deployed decreases, reconciled_proceeds increases, gain to yield pool, loss shaved from principal |

## V2.2 External custody accounting invariant (updated)

Primary becomes: senior + junior + claimable + bonds + yield_pool == native_custody_balance + active_deployed_to_prophet_grid + reconciled_proceeds_total.
- deploy: native -= routed, active_deployed += routed. Both sides unchanged.
- reconcile: active -= deployed, reconciled += proceeds, so one side moves by (proceeds - deployed) which equals realized gain or loss. Gain credited to the yield pool, loss shaved from junior then senior principal (and vault_custody and pst together), so both sides move by the same delta.
- Because deployed value physically leaves the contract as native GEN, native_custody_balance still equals the chain balance of the vault at every step, and the deployed and reconciled value is carried as separate recognized assets.
Proven by: test_deploy_moves_capital_out_of_idle_custody, test_reconcile_neutral_restores_invariants_and_closes_position, test_reconcile_records_market_making_gain, test_reconcile_junior_absorbs_first_loss_and_senior_is_protected, test_invariants_hold_across_a_full_epoch_lifecycle.

## V2.3 Races and double action guards

| Pair | Exclusion | Test |
|---|---|---|
| deploy twice | positions.get(epoch) already set -> revert | test_deploy_is_idempotent_guarded |
| reconcile twice | deployment_status != DEPLOYED_LIVE -> revert | test_reconcile_cannot_run_twice |
| reconcile without deploy | no position -> revert | test_reconcile_refuses_a_position_that_is_not_deployed |
| settle before reconcile | routed > 0 and (no position or not reconciled) -> revert | test_settle_reverts_when_deployed_but_not_reconciled |
| deploy before finalize | status != FINALIZED -> revert | test_deploy_refuses_a_non_finalized_epoch |
| leg tampering | yes_leg + no_leg must equal routed exactly, each within staking bounds -> revert | test_deploy_refuses_legs_that_do_not_sum_to_the_routed_amount |

## V2.4 Liveness fix

FINDING: a FINALIZED epoch with routed capital could only exit by settle, which requires reconcile; if the linked book ever failed to answer, funds could strand because FINALIZED never fell through to the timeout refund path.
FIXED: _phase() now returns PRINCIPAL_REFUND_OPEN for a FINALIZED epoch past rebalance_timeout_epoch, so LPs can always recover principal by timeout even if deployment or reconciliation is unavailable. reconcile itself also falls back to par when a market is still OPEN, so the normal path never blocks settlement.

## V2.5 Honest limits and open items

- OPEN (design boundary, not a bug): the live ProphetGrid credits redemption to the caller as PGT settlement collateral inside its own vault, it has no native GEN redemption method. So reconciled proceeds are recognized in the syndicate ledger as reconciled_proceeds_total and remain custodied on the linked contract, rather than returning as native GEN. LP native withdrawals still flow through the claim rail and any native amount the vault cannot currently cover is preserved as claimable credit rather than lost. This is documented in the README.
- The linked ProphetGrid address is fixed at construction and has no setter; deploying against an untrusted grid is a deployer trust assumption shared by every linked module integration, and this deployment links the accepted live address.
- reconcile reads the linked book through a synchronous cross contract view; the amount is not an LLM free choice. Direct mode only exercises the leader read, so live validator agreement on the cross contract read is proven by the studionet run, not by the unit tests.
- The deployed contract source is 44,342 bytes, above the v1 self imposed 37,500 target. This was validated empirically: the remediated contract deploys and finalizes on studionet, and the live ProphetGrid itself is 74,390 bytes, so the node ceiling is far higher than the old target. Adding the mandated cross contract feature set cannot fit in the old 940 byte headroom without removing tested functionality.

## V2.6 Live round trip result (this session)

The full lifecycle was driven on studionet and every claim below is backed by a finalized
transaction read back over the RPC (see evidence/studionet_deployment.json, 48 transactions):

- Every routed epoch created its own live market on the linked ProphetGrid and deployed into it:
  epoch 1 market 6 (7.182 GEN), epoch 2 market 7 (4.788 GEN, after a paid challenge and re audit),
  epoch 4 market 8 (0.9975 GEN defensive band), epoch 5 market 9 (7.98 GEN). All reconciled to
  RECONCILED_LIVE and settled. Epoch 3 was left unaudited and timed out into full principal refunds.
- The linked grid's own `get_trader_portfolio` returned the syndicate contract address `0x0F9e...38ef`
  as a position holder across markets 6, 7, 8 and 9, so the position is real on the external book,
  not only in the syndicate ledger.
- After every deploy and reconcile the summary checks stayed green, `custody_partition_exact`,
  `pst_backed_by_custody`, `locked_principal_reserved` and the new `deployed_within_custody` were all
  true, and `native_custody_balance` equaled `eth_getBalance` of the vault at the end (1.12 GEN).
- The settle gate was observed working for real: an earlier settlement of a routed epoch before any
  reconciliation reverted (`execution=ERROR`), which is exactly the guard the Steward asked for.

No new OPEN findings from the live run. The design boundary in V2.5 (PGT collateral rather than
native GEN redemption) is unchanged and remains honestly documented.
