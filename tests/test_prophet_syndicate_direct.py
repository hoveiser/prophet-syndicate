"""Direct mode tests for contracts/prophet_syndicate.py.

Run with: .venv/bin/pytest tests/test_prophet_syndicate_direct.py -q

Direct mode executes the leader path only, so these tests cover custody,
temporal precedence, soft fail refunds, the routing model and settlement
mathematics. Validator agreement on the seven consensus fields, live feed
reachability and native payouts are proven by the studionet transactions in
evidence/studionet_deployment.json.
"""

from conftest import DEEP, GEN, SHALLOW, MockGrid, at, deploy_and_reconcile, install_grid, mock_feeds, payload, split_receipt, state_for

BOND = 10 ** 16
FEE_BPS = 25
T0 = "2026-11-01T12:00:00Z"

# Feed documents chosen so the deterministic model lands on a known band.
DEEP_BOOK = ("""{"markets": [{"total_pot": 6000000, "yes_pool": 3000000, "no_pool": 3000000}]}""")
TILTED_BOOK = """{"markets": [{"total_pot": 1600000, "yes_pool": 1200000, "no_pool": 400000}]}"""
SHOCKED_BOOK = """{"markets": [{"total_pot": 1000000, "yes_pool": 650000, "no_pool": 350000}]}"""
DRY_BOOK = """{"markets": [{"total_pot": 100000, "yes_pool": 95000, "no_pool": 5000}]}"""


def _iso(epoch):
    """ISO instant for the fixed UTC day the window tests are anchored to."""
    rest = epoch - 1793491800
    hour, minute, second = rest // 3600, rest % 3600 // 60, rest % 60
    return "2026-11-01T%02d:%02d:%02dZ" % (hour, minute, second)


def macro(volatility):
    return """{"chart": {"result": [{"meta": {"symbol": "^VIX", "regularMarketPrice": %s}}]}}""" % volatility


def opened(direct_vm, syndicate, alice, reference=DEEP):
    """Open epoch 1 and return its state."""
    direct_vm.sender = alice
    direct_vm.value = BOND
    at(direct_vm, T0)
    syndicate.propose_routing_epoch_payable(reference)
    return state_for(syndicate, 1)


def funded(direct_vm, syndicate, alice, senior=GEN, junior=0, bob=None, reference=DEEP):
    opened(direct_vm, syndicate, alice, reference)
    direct_vm.value = senior
    if senior:
        split_receipt(syndicate.subscribe_senior_tranche_payable())
    if junior:
        direct_vm.sender = bob
        direct_vm.value = junior
        split_receipt(syndicate.subscribe_junior_tranche_payable())
        direct_vm.sender = alice


def audited(direct_vm, syndicate, alice, bob=None, volatility="18.53", book=DEEP_BOOK, reference=DEEP, senior=GEN):
    funded(direct_vm, syndicate, alice, senior, 0, bob, reference)
    mock_feeds(direct_vm, macro(volatility), book)
    at(direct_vm, "2026-11-01T12:11:00Z")
    syndicate.execute_consensus_rebalance(1)
    return state_for(syndicate, 1)


# ----------------------------------------------------------------------
# Deployment
# ----------------------------------------------------------------------


def test_deploy_links_the_accepted_prophet_grid_deployment(syndicate):
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["linked_prophet_grid_address"] == "0xb0aedbb6e8056c0a2c90e4bd901bb0f3da6bb967"
    assert summary["linked_probe_status"] == "NOT_PROBED"


def test_deploy_publishes_the_pinned_feeds_and_runner(syndicate):
    summary = payload(syndicate.get_custody_accounting_summary())
    assert "query1.finance.yahoo.com" in summary["macro_feed_url"]
    assert "prophet-grid" in summary["market_depth_url"]
    assert summary["runner_pin"] == "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6"


def test_deploy_starts_with_an_empty_but_consistent_vault(syndicate):
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["ok"] is True
    assert summary["native_custody_balance"] == "0"
    assert summary["pst_total_supply"] == "0"
    assert summary["epochs"] == []


def test_deploy_publishes_the_deployed_windows_and_bonds(syndicate):
    limits = payload(syndicate.get_custody_accounting_summary())["limits"]
    assert limits["subscription_seconds"] == "600"
    assert limits["challenge_seconds"] == "900"
    assert limits["audit_timeout_seconds"] == "1800"
    assert int(limits["routing_bond_wei"]) == BOND
    assert int(limits["challenge_bond_wei"]) == 2 * BOND
    assert int(limits["entry_fee_bps"]) == FEE_BPS


def test_deploy_refuses_a_tranche_cap_below_the_floor(direct_deploy):
    try:
        direct_deploy(
            "contracts/prophet_syndicate.py", "", "", "", 5, BOND, 600, 900, 1800
        )
        raise AssertionError("an out of range tranche cap must be refused")
    except Exception as exc:
        assert "floor or ceiling" in str(exc)


def test_deploy_refuses_a_non_https_macro_feed(direct_deploy):
    try:
        direct_deploy(
            "contracts/prophet_syndicate.py", "", "http://cdn.cboe.com/vix.json", "",
            40 * GEN, BOND, 600, 900, 1800,
        )
        raise AssertionError("a non https feed must be refused")
    except Exception as exc:
        assert "https" in str(exc)


def test_deploy_refuses_a_single_label_depth_host(direct_deploy):
    try:
        direct_deploy(
            "contracts/prophet_syndicate.py", "", "", "https://intranet/depth.json",
            40 * GEN, BOND, 600, 900, 1800,
        )
        raise AssertionError("an unqualified host must be refused")
    except Exception as exc:
        assert "qualified" in str(exc)


def test_deploy_refuses_a_feed_with_credentials(direct_deploy):
    try:
        direct_deploy(
            "contracts/prophet_syndicate.py", "", "https://user@cdn.cboe.com/vix.json", "",
            40 * GEN, BOND, 600, 900, 1800,
        )
        raise AssertionError("credentials in a feed URL must be refused")
    except Exception as exc:
        assert "plain https" in str(exc)


def test_deploy_accepts_a_custom_link_and_feeds(direct_deploy):
    contract = direct_deploy(
        "contracts/prophet_syndicate.py",
        "0x1111111111111111111111111111111111111111",
        "https://feed.example.com/vix.json",
        "https://books.example.com/depth.json",
        40 * GEN, BOND, 600, 900, 1800,
    )
    summary = payload(contract.get_custody_accounting_summary())
    assert summary["linked_prophet_grid_address"] == "0x1111111111111111111111111111111111111111"
    assert summary["macro_feed_url"] == "https://feed.example.com/vix.json"


# ----------------------------------------------------------------------
# propose_routing_epoch_payable
# ----------------------------------------------------------------------


def test_propose_opens_the_first_sequential_epoch(direct_vm, syndicate, direct_alice):
    state = opened(direct_vm, syndicate, direct_alice)
    assert state["epoch_id"] == "1"
    assert state["status"] == "SUBSCRIPTION_OPEN"
    assert state["phase"] == "COLLECTING_SUBSCRIPTIONS"
    assert state["subscription_close_epoch"] == "1793535000"
    assert int(state["senior_floor_wei"]) == 0


def test_propose_locks_the_routing_bond_and_returns_the_surplus(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = 4 * BOND
    at(direct_vm, T0)
    syndicate.propose_routing_epoch_payable(DEEP)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["partition"]["locked_bond_wei"] == str(BOND)
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == 3 * BOND


def test_propose_with_a_short_bond_is_soft_failed_and_claimable(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = BOND // 3
    at(direct_vm, T0)
    result = payload(syndicate.propose_routing_epoch_payable(DEEP))
    assert result["accepted"] is False
    assert "bond" in result["reason"]
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == BOND // 3


def test_propose_with_no_value_records_the_rejection_only(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    at(direct_vm, T0)
    result = payload(syndicate.propose_routing_epoch_payable(DEEP))
    assert result["accepted"] is False
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == 0
    position = payload(syndicate.get_tranche_position(str(direct_alice)))
    assert position["last_rejection"].startswith("[SOFT-FAIL]")


def test_propose_refuses_a_second_epoch_while_one_is_in_flight(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = BOND
    at(direct_vm, "2026-11-01T12:05:00Z")
    result = payload(syndicate.propose_routing_epoch_payable(DEEP))
    assert result["accepted"] is False
    assert "in flight" in result["reason"]


def test_propose_refuses_a_depth_reference_below_the_floor(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = BOND
    at(direct_vm, T0)
    result = payload(syndicate.propose_routing_epoch_payable(7))
    assert result["accepted"] is False
    assert "floor or ceiling" in result["reason"]
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == BOND


def test_propose_stamps_every_window_from_the_transaction_datetime(direct_vm, syndicate, direct_alice):
    state = opened(direct_vm, syndicate, direct_alice)
    opened_at = 1793534400
    assert int(state["subscription_close_epoch"]) == opened_at + 600
    assert int(state["rebalance_challenge_deadline_epoch"]) == opened_at + 1500
    assert int(state["rebalance_timeout_epoch"]) == opened_at + 2400


# ----------------------------------------------------------------------
# Subscriptions
# ----------------------------------------------------------------------


def test_senior_subscription_splits_principal_and_entry_fee(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    parts = split_receipt(syndicate.subscribe_senior_tranche_payable())
    assert parts[0] == "SENIOR"
    assert int(parts[1]) == GEN - GEN * FEE_BPS // 10000
    assert int(parts[2]) == GEN * FEE_BPS // 10000


def test_junior_subscription_uses_the_junior_ledger(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 2 * GEN
    split_receipt(syndicate.subscribe_junior_tranche_payable())
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["partition"]["junior_principal_wei"] == str(2 * GEN - 2 * GEN * FEE_BPS // 10000)
    assert summary["partition"]["yield_pool_wei"] == str(2 * GEN * FEE_BPS // 10000)


def test_two_lps_two_tranches_and_the_pst_supply(direct_vm, syndicate, direct_alice, direct_bob):
    funded(direct_vm, syndicate, direct_alice, 8 * GEN, 2 * GEN, direct_bob)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["ok"] is True
    assert summary["pst_total_supply"] == summary["vault_custody_balance"]
    position = payload(syndicate.get_tranche_position(str(direct_bob)))
    assert int(position["junior_principal_wei"]) > 0
    assert position["senior_principal_wei"] == "0"


def test_subscription_with_no_value_is_soft_failed(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 0
    result = payload(syndicate.subscribe_senior_tranche_payable())
    assert result["accepted"] is False
    assert "no native value" in result["reason"]


def test_subscription_below_the_minimum_is_refunded_not_lost(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 10 ** 12
    result = payload(syndicate.subscribe_junior_tranche_payable())
    assert result["accepted"] is False
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == 10 ** 12
    assert payload(syndicate.get_custody_accounting_summary())["ok"] is True


def test_subscription_over_the_tranche_cap_is_soft_failed(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 60 * GEN
    result = payload(syndicate.subscribe_senior_tranche_payable())
    assert result["accepted"] is False
    assert "cap" in result["reason"]
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["partition"]["senior_principal_wei"] == "0"
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == 60 * GEN


def test_subscription_after_the_window_closes_is_soft_failed(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    at(direct_vm, "2026-11-01T12:10:01Z")
    result = payload(syndicate.subscribe_senior_tranche_payable())
    assert result["accepted"] is False
    assert "subscription window" in result["reason"]


def test_subscription_before_any_epoch_is_soft_failed(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = GEN
    at(direct_vm, T0)
    result = payload(syndicate.subscribe_junior_tranche_payable())
    assert result["accepted"] is False


def test_a_rejected_call_never_reverts_and_keeps_custody_exact(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 7
    payload(syndicate.subscribe_senior_tranche_payable())
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["checks"]["custody_partition_exact"] is True
    assert int(summary["native_custody_balance"]) == BOND + 7


def test_a_successful_call_clears_the_previous_rejection(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 7
    payload(syndicate.subscribe_senior_tranche_payable())
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    position = payload(syndicate.get_tranche_position(str(direct_alice)))
    assert position["last_rejection"] == ""


def test_pst_receipts_mirror_the_locked_principal_exactly(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 3 * GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_junior_tranche_payable())
    summary = payload(syndicate.get_custody_accounting_summary())
    senior = int(summary["partition"]["senior_principal_wei"])
    junior = int(summary["partition"]["junior_principal_wei"])
    assert int(summary["pst_total_supply"]) == senior + junior
    position = payload(syndicate.get_tranche_position(str(direct_alice)))
    assert int(position["pst_balance_wei"]) == senior + junior


# ----------------------------------------------------------------------
# execute_consensus_rebalance
# ----------------------------------------------------------------------


def test_rebalance_is_refused_before_the_window_closes(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    at(direct_vm, "2026-11-01T12:05:00Z")
    try:
        syndicate.execute_consensus_rebalance(1)
        raise AssertionError("rebalance must wait for the window to close")
    except Exception as exc:
        assert "rebalance" in str(exc)


def test_rebalance_is_refused_after_the_audit_timeout(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    at(direct_vm, "2026-11-01T13:00:00Z")
    try:
        syndicate.execute_consensus_rebalance(1)
        raise AssertionError("an un audited epoch must fall through to refunds")
    except Exception as exc:
        assert "rebalance" in str(exc)


def test_rebalance_refuses_an_unknown_epoch(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    at(direct_vm, "2026-11-01T12:11:00Z")
    try:
        syndicate.execute_consensus_rebalance(99)
        raise AssertionError("a missing epoch must revert")
    except Exception as exc:
        assert "does not exist" in str(exc)


def test_high_conviction_band_routes_eighty_percent_to_market_making(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice, volatility="12.0", book=DEEP_BOOK)
    assert state["routing_band"] == "HIGH_CONVICTION_DEPLOYMENT"
    assert state["quantized_volatility_baseline_bps"] == "1250"
    assert state["quantized_tail_risk_bps"] == "0"
    liquidity = int(state["senior_commit_wei"]) + int(state["junior_commit_wei"])
    assert int(state["derived_active_deployment_amount"]) == liquidity * 8000 // 10000
    assert int(state["derived_senior_reserve_buffer"]) == liquidity - liquidity * 8000 // 10000


def test_balanced_band_routes_half_and_hedges_half(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice, volatility="18.53", book=TILTED_BOOK)
    assert state["routing_band"] == "BALANCED_HEDGE_ALLOCATION"
    assert state["quantized_tail_risk_bps"] == "2500"
    liquidity = int(state["senior_commit_wei"]) + int(state["junior_commit_wei"])
    assert int(state["derived_active_deployment_amount"]) == liquidity // 2


def test_defensive_band_caps_new_exposure_at_twenty_percent(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice, volatility="61.0", book=SHOCKED_BOOK, reference=10 ** 7)
    assert state["routing_band"] == "DEFENSIVE_COLLATERAL_BUFFER"
    liquidity = int(state["senior_commit_wei"]) + int(state["junior_commit_wei"])
    assert int(state["derived_active_deployment_amount"]) == liquidity * 2000 // 10000
    assert int(state["derived_senior_reserve_buffer"]) > int(state["derived_active_deployment_amount"])


def test_freeze_band_opens_penalty_free_principal_refunds(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice, volatility="95.0", book=DRY_BOOK, reference=SHALLOW)
    assert state["routing_band"] == "CORRELATION_FREEZE_WITHDRAWAL"
    assert state["derived_active_deployment_amount"] == "0"
    assert state["status"] == "REFUNDABLE_ZERO_UTILIZATION"
    assert state["phase"] == "PRINCIPAL_REFUND_OPEN"
    at(direct_vm, "2026-11-01T12:30:00Z")
    refund = payload(syndicate.claim_epoch_principal_refund(1))
    assert int(refund["refunded_principal_wei"]) > 0
    assert refund["status"] == "PRINCIPAL_RELEASED"


def test_consensus_fields_are_stored_verbatim_for_the_audit_trail(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    for name in (
        "routing_band", "quantized_volatility_baseline_bps", "quantized_tail_risk_bps",
        "derived_active_deployment_amount", "derived_senior_reserve_buffer",
        "derived_senior_yield_bps", "derived_junior_yield_bps",
    ):
        assert state[name] != ""
    assert int(state["derived_senior_yield_bps"]) >= 250
    assert int(state["derived_junior_yield_bps"]) >= 1000


def test_routing_conserves_liquidity_across_every_band(direct_vm, syndicate, direct_alice):
    for volatility, book, epoch_id in (("9.0", DEEP_BOOK, 1), ("26.0", TILTED_BOOK, 2), ("61.0", SHOCKED_BOOK, 3)):
        direct_vm.value = BOND
        base = 1793534400 + 3600 * epoch_id
        at(direct_vm, _iso(base))
        syndicate.propose_routing_epoch_payable(DEEP)
        direct_vm.value = GEN
        split_receipt(syndicate.subscribe_senior_tranche_payable())
        direct_vm.clear_mocks()
        mock_feeds(direct_vm, macro(volatility), book)
        at(direct_vm, _iso(base + 700))
        syndicate.execute_consensus_rebalance(epoch_id)
        state = state_for(syndicate, epoch_id)
        assert int(state["derived_active_deployment_amount"]) + int(state["derived_senior_reserve_buffer"]) == int(
            state["senior_commit_wei"]
        ) + int(state["junior_commit_wei"])
        at(direct_vm, _iso(base + 1700))
        deploy_and_reconcile(syndicate, epoch_id)
        syndicate.settle_tranche_epoch(epoch_id)


def test_unusable_macro_feed_routes_no_capital(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    mock_feeds(direct_vm, """{"data": {"symbol": "_VIX"}}""", DEEP_BOOK)
    at(direct_vm, "2026-11-01T12:11:00Z")
    try:
        syndicate.execute_consensus_rebalance(1)
        raise AssertionError("a feed with no volatility print must not route capital")
    except Exception as exc:
        assert "[EXTERNAL]" in str(exc)


def test_unreadable_depth_feed_routes_no_capital(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    mock_feeds(direct_vm, macro("18.53"), """{"markets": []}""")
    at(direct_vm, "2026-11-01T12:11:00Z")
    try:
        syndicate.execute_consensus_rebalance(1)
        raise AssertionError("no measurable open interest must not route capital")
    except Exception as exc:
        assert "open interest" in str(exc) or "too little readable text" in str(exc)


def test_a_rejected_consensus_answer_leaves_the_epoch_untouched(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    mock_feeds(direct_vm, macro("18.53"), """{"markets": [{"total_pot": 0, "yes_pool": 0, "no_pool": 0}]}""")
    at(direct_vm, "2026-11-01T12:11:00Z")
    try:
        syndicate.execute_consensus_rebalance(1)
    except Exception:
        pass
    state = state_for(syndicate, 1)
    assert state["status"] == "SUBSCRIPTION_OPEN"
    assert state["routing_band"] == ""


def test_the_linkage_probe_reports_the_linked_market_count(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["linked_probe_status"].startswith(("VERIFIED_", "UNAVAILABLE"))


def test_rebalance_snapshots_the_commitments_taken_at_the_window_close(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    assert int(state["senior_commit_wei"]) == GEN - GEN * FEE_BPS // 10000
    assert state["status"] == "FINALIZED"
    assert int(state["senior_floor_wei"]) > 0


def test_executing_twice_is_refused_once_the_epoch_is_finalized(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    at(direct_vm, "2026-11-01T12:20:00Z")
    try:
        syndicate.execute_consensus_rebalance(1)
        raise AssertionError("a finalized epoch is not awaiting an audit")
    except Exception as exc:
        assert "rebalance" in str(exc)


# ----------------------------------------------------------------------
# challenge_routing_allocation_payable
# ----------------------------------------------------------------------


def test_challenge_requires_a_finalized_epoch_inside_its_window(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 2 * BOND
    at(direct_vm, "2026-11-01T12:05:00Z")
    result = payload(syndicate.challenge_routing_allocation_payable(1))
    assert result["accepted"] is False
    assert "challenge slot" in result["reason"]


def test_challenge_buys_a_reaudit_and_funds_the_yield_pool(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    at(direct_vm, "2026-11-01T12:20:00Z")
    direct_vm.value = 2 * BOND
    syndicate.challenge_routing_allocation_payable(1)
    state = state_for(syndicate, 1)
    assert state["status"] == "CHALLENGED"
    assert state["phase"] == "AWAITING_CONSENSUS_AUDIT"
    summary = payload(syndicate.get_custody_accounting_summary())
    assert int(summary["partition"]["yield_pool_wei"]) > 0


def test_challenge_reopens_the_audit_gate(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    at(direct_vm, "2026-11-01T12:20:00Z")
    direct_vm.value = 4 * BOND
    syndicate.challenge_routing_allocation_payable(1)
    at(direct_vm, "2026-11-01T12:21:00Z")
    mock_feeds(direct_vm, macro("24.0"), TILTED_BOOK)
    syndicate.execute_consensus_rebalance(1)
    state = state_for(syndicate, 1)
    assert state["status"] in ("FINALIZED", "REFUNDABLE_ZERO_UTILIZATION")
    assert int(state["challenge_count"]) == 1


def test_a_short_challenge_bond_stays_claimable(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    at(direct_vm, "2026-11-01T12:20:00Z")
    direct_vm.value = 10
    result = payload(syndicate.challenge_routing_allocation_payable(1))
    assert result["accepted"] is False
    assert int(syndicate.get_refundable_balance(str(direct_alice))) >= 10


def test_challenge_slots_are_capped(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    for index in range(3):
        at(direct_vm, "2026-11-01T12:20:00Z")
        direct_vm.value = 4 * BOND
        result = syndicate.challenge_routing_allocation_payable(1)
        if index == 2:
            assert payload(result)["accepted"] is False
        else:
            at(direct_vm, "2026-11-01T12:21:00Z")
            mock_feeds(direct_vm, macro("24.0"), TILTED_BOOK)
            syndicate.execute_consensus_rebalance(1)
    assert int(state_for(syndicate, 1)["challenge_count"]) == 2


# ----------------------------------------------------------------------
# settle_tranche_epoch
# ----------------------------------------------------------------------


def test_settlement_is_refused_while_the_challenge_window_is_open(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    at(direct_vm, "2026-11-01T12:20:00Z")
    try:
        syndicate.settle_tranche_epoch(1)
        raise AssertionError("settlement must wait for the challenge deadline")
    except Exception as exc:
        assert "challenge" in str(exc)


def test_settlement_is_refused_for_an_unfinalized_epoch(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    at(direct_vm, "2026-11-01T12:11:00Z")
    try:
        syndicate.settle_tranche_epoch(1)
        raise AssertionError("only a finalized epoch may settle")
    except Exception as exc:
        assert "settlement" in str(exc) or "finalized" in str(exc)


def test_settlement_releases_principal_and_pool_funded_yield(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    report = payload(syndicate.settle_tranche_epoch(1))
    assert report["settled"] is True
    assert int(report["senior_principal_wei"]) == GEN - GEN * FEE_BPS // 10000
    assert int(report["senior_yield_wei"]) + int(report["junior_yield_wei"]) > 0
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["ok"] is True
    assert summary["partition"]["senior_principal_wei"] == "0"
    assert summary["pst_total_supply"] == "0"


def test_settlement_cannot_pay_more_yield_than_the_pool_holds(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    before = payload(syndicate.get_custody_accounting_summary())
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    report = payload(syndicate.settle_tranche_epoch(1))
    after = payload(syndicate.get_custody_accounting_summary())
    paid = int(report["senior_yield_wei"]) + int(report["junior_yield_wei"])
    assert paid <= int(before["partition"]["yield_pool_wei"])
    assert int(before["partition"]["yield_pool_wei"]) - int(after["partition"]["yield_pool_wei"]) == paid
    assert after["ok"] is True


def test_senior_yield_is_paid_before_any_junior_yield(direct_vm, syndicate, direct_alice, direct_bob):
    funded(direct_vm, syndicate, direct_alice, 4 * GEN, 2 * GEN, direct_bob)
    mock_feeds(direct_vm, macro("12.0"), DEEP_BOOK)
    at(direct_vm, "2026-11-01T12:11:00Z")
    syndicate.execute_consensus_rebalance(1)
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    report = payload(syndicate.settle_tranche_epoch(1))
    assert int(report["senior_yield_wei"]) > 0
    assert payload(syndicate.get_refundable_balance(str(direct_alice))) > 4 * GEN - 4 * GEN * FEE_BPS // 10000


def test_an_epoch_settles_only_once(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    syndicate.settle_tranche_epoch(1)
    try:
        syndicate.settle_tranche_epoch(1)
        raise AssertionError("an epoch may only settle once")
    except Exception as exc:
        assert "settlement" in str(exc)


def test_settlement_returns_the_routing_bond_to_the_proposer(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    syndicate.settle_tranche_epoch(1)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["partition"]["locked_bond_wei"] == "0"
    assert int(syndicate.get_refundable_balance(str(direct_alice))) >= BOND


def test_a_settled_epoch_stops_reserving_liability(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    reserved_before = int(payload(syndicate.get_custody_accounting_summary())["reserved_liability_wei"])
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    syndicate.settle_tranche_epoch(1)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert reserved_before > 0
    assert summary["reserved_liability_wei"] == "0"
    assert summary["ok"] is True


# ----------------------------------------------------------------------
# claim_epoch_principal_refund
# ----------------------------------------------------------------------


def test_unaudited_epoch_returns_one_hundred_percent_of_principal(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = 3 * GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    at(direct_vm, "2026-11-01T13:00:00Z")
    result = payload(syndicate.claim_epoch_principal_refund(1))
    assert result["refunded_principal_wei"] == str(3 * GEN - 3 * GEN * FEE_BPS // 10000)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["ok"] is True
    assert summary["partition"]["senior_principal_wei"] == "0"


def test_principal_refund_is_locked_out_during_the_audit_window(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    at(direct_vm, "2026-11-01T12:10:01Z")
    try:
        syndicate.claim_epoch_principal_refund(1)
        raise AssertionError("the audit window must stay closed to refunds")
    except Exception as exc:
        assert "principal refund" in str(exc)


def test_principal_refund_cannot_be_claimed_twice(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    at(direct_vm, "2026-11-01T13:00:00Z")
    payload(syndicate.claim_epoch_principal_refund(1))
    try:
        syndicate.claim_epoch_principal_refund(1)
        raise AssertionError("a released epoch must not refund twice")
    except Exception as exc:
        assert "principal refund" in str(exc) or "no locked principal" in str(exc)


def test_a_missing_audit_forfeits_the_routing_bond_to_lp_yield(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    at(direct_vm, "2026-11-01T13:00:00Z")
    payload(syndicate.claim_epoch_principal_refund(1))
    pool = int(payload(syndicate.get_custody_accounting_summary())["partition"]["yield_pool_wei"])
    assert pool == GEN * FEE_BPS // 10000 + BOND


def test_settled_epochs_are_not_refundable(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    syndicate.settle_tranche_epoch(1)
    try:
        syndicate.claim_epoch_principal_refund(1)
        raise AssertionError("a settled epoch has no principal left to refund")
    except Exception as exc:
        assert "principal refund" in str(exc) or "no locked principal" in str(exc)


# ----------------------------------------------------------------------
# The claim rail
# ----------------------------------------------------------------------


def test_withdraw_refund_without_a_credit_reports_the_reason(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    result = payload(syndicate.withdraw_refund(GEN))
    assert result["paid_wei"] == "0"
    position = payload(syndicate.get_tranche_position(str(direct_alice)))
    assert position["last_rejection"].startswith("[SOFT-FAIL]")


def test_withdraw_refund_moves_the_credit_and_the_custody(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = BOND // 2
    at(direct_vm, T0)
    payload(syndicate.propose_routing_epoch_payable(DEEP))
    owed = int(syndicate.get_refundable_balance(str(direct_alice)))
    before = int(payload(syndicate.get_custody_accounting_summary())["native_custody_balance"])
    result = payload(syndicate.withdraw_refund(owed))
    assert int(result["paid_wei"]) == owed
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == 0
    summary = payload(syndicate.get_custody_accounting_summary())
    assert int(summary["native_custody_balance"]) == before - owed
    assert summary["ok"] is True


def test_claim_refundable_balance_sweeps_the_whole_credit(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = BOND // 5
    at(direct_vm, T0)
    payload(syndicate.propose_routing_epoch_payable(DEEP))
    assert int(syndicate.claim_refundable_balance()) == BOND // 5
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == 0


def test_partial_withdraw_never_over_draws_the_credit(direct_vm, syndicate, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = 3 * BOND
    at(direct_vm, T0)
    syndicate.propose_routing_epoch_payable(DEEP)
    owed = int(syndicate.get_refundable_balance(str(direct_alice)))
    payload(syndicate.withdraw_refund(owed // 4))
    assert int(syndicate.get_refundable_balance(str(direct_alice))) == owed - owed // 4


def test_get_refundable_balance_validates_the_address(direct_vm, syndicate):
    try:
        syndicate.get_refundable_balance("0xnope")
        raise AssertionError("a malformed address must be refused")
    except Exception as exc:
        assert "42 character hex" in str(exc)


# ----------------------------------------------------------------------
# Invariants and views
# ----------------------------------------------------------------------


def test_custody_partition_equals_native_custody_at_every_step(direct_vm, syndicate, direct_alice, direct_bob):
    funded(direct_vm, syndicate, direct_alice, 2 * GEN, GEN, direct_bob)
    direct_vm.sender = direct_alice
    direct_vm.value = 10 ** 12
    payload(syndicate.subscribe_junior_tranche_payable())
    summary = payload(syndicate.get_custody_accounting_summary())
    buckets = sum(int(value) for value in summary["partition"].values())
    assert buckets == int(summary["native_custody_balance"])
    assert summary["checks"]["custody_partition_exact"] is True


def test_locked_principal_is_reserved_and_the_floor_gap_is_reported(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    summary = payload(syndicate.get_custody_accounting_summary())
    reserved = int(summary["reserved_liability_wei"])
    assert reserved == int(state["senior_commit_wei"]) + int(state["junior_commit_wei"])
    assert int(summary["unfunded_floor_wei"]) >= 0
    assert int(summary["unfunded_floor_wei"]) == max(
        0, int(state["senior_floor_wei"]) - int(summary["partition"]["yield_pool_wei"])
    )
    assert summary["checks"]["locked_principal_reserved"] is True


def test_the_vault_is_always_backed_one_to_one(direct_vm, syndicate, direct_alice):
    funded(direct_vm, syndicate, direct_alice, 6 * GEN)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["checks"]["pst_backed_by_custody"] is True
    assert int(summary["vault_custody_balance"]) == int(summary["pst_total_supply"])


def test_position_view_exposes_the_live_epoch_and_balances(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    position = payload(syndicate.get_tranche_position(str(direct_alice)))
    assert position["active_epoch"]["epoch_id"] == "1"
    assert position["active_epoch"]["phase"] == "COLLECTING_SUBSCRIPTIONS"
    assert int(position["senior_principal_wei"]) > 0


def test_summary_lists_every_epoch_with_its_phase(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    syndicate.settle_tranche_epoch(1)
    syndicate.propose_routing_epoch_payable(DEEP)
    rows = payload(syndicate.get_custody_accounting_summary())["epochs"]
    assert [row["epoch_id"] for row in rows] == ["1", "2"]
    assert rows[0]["phase"] == "RELEASED"
    assert rows[1]["phase"] == "COLLECTING_SUBSCRIPTIONS"


def test_a_second_epoch_opens_only_after_the_first_is_released(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    direct_vm.value = BOND
    at(direct_vm, "2026-11-01T12:30:00Z")
    result = payload(syndicate.propose_routing_epoch_payable(DEEP))
    assert result["accepted"] is False
    deploy_and_reconcile(syndicate, 1)
    at(direct_vm, "2026-11-01T12:30:01Z")
    syndicate.settle_tranche_epoch(1)
    direct_vm.value = BOND
    syndicate.propose_routing_epoch_payable(DEEP)
    assert state_for(syndicate, 2)["status"] == "SUBSCRIPTION_OPEN"


def test_invariants_hold_across_a_full_epoch_lifecycle(direct_vm, syndicate, direct_alice, direct_bob):
    funded(direct_vm, syndicate, direct_alice, 4 * GEN, GEN, direct_bob)
    mock_feeds(direct_vm, macro("18.53"), TILTED_BOOK)
    at(direct_vm, "2026-11-01T12:11:00Z")
    syndicate.execute_consensus_rebalance(1)
    at(direct_vm, "2026-11-01T12:20:00Z")
    direct_vm.value = 3 * BOND
    syndicate.challenge_routing_allocation_payable(1)
    at(direct_vm, "2026-11-01T12:21:00Z")
    mock_feeds(direct_vm, macro("18.53"), TILTED_BOOK)
    syndicate.execute_consensus_rebalance(1)
    at(direct_vm, "2026-11-01T12:40:00Z")
    deploy_and_reconcile(syndicate, 1)
    syndicate.settle_tranche_epoch(1)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert summary["ok"] is True
    assert summary["epoch_count"] == "1"
    assert int(summary["epoch_count"]) >= 1
    bucketed = sum(int(value) for value in summary["partition"].values())
    assets = int(summary["native_custody_balance"]) + int(summary["active_deployed_to_prophet_grid"]) + int(summary["reconciled_proceeds_total"])
    assert bucketed == assets == int(summary["total_custody_assets_wei"])
    assert int(summary["reconciled_proceeds_total"]) > 0
    assert summary["checks"]["custody_partition_exact"] is True


# ----------------------------------------------------------------------
# Real ProphetGrid cross contract deployment, position, and reconciliation
# ----------------------------------------------------------------------


def _leg_split(state):
    routed = int(state["derived_active_deployment_amount"])
    return routed, routed // 2, routed - routed // 2


def test_settle_reverts_when_routed_capital_is_not_deployed(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    at(direct_vm, "2026-11-01T12:30:01Z")
    try:
        syndicate.settle_tranche_epoch(1)
        raise AssertionError("settlement must be blocked until ProphetGrid reconciliation")
    except Exception as exc:
        assert "reconciled" in str(exc) and "deployed" in str(exc)


def test_settle_reverts_when_deployed_but_not_reconciled(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    routed, yes_leg, no_leg = _leg_split(state)
    install_grid(MockGrid(ratio_bps=10000))
    syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg, no_leg)
    at(direct_vm, "2026-11-01T12:30:01Z")
    try:
        syndicate.settle_tranche_epoch(1)
        raise AssertionError("an unreconciled live position must not settle")
    except Exception as exc:
        assert "reconciled" in str(exc)


def test_deploy_moves_capital_out_of_idle_custody(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    routed, yes_leg, no_leg = _leg_split(state)
    before = int(payload(syndicate.get_custody_accounting_summary())["native_custody_balance"])
    install_grid(MockGrid())
    receipt = payload(syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg, no_leg))
    summary = payload(syndicate.get_custody_accounting_summary())
    position = payload(syndicate.get_epoch_prophet_grid_position(1))
    assert receipt["deployment_status"] == "DEPLOYED_LIVE"
    assert int(summary["native_custody_balance"]) == before - routed
    assert int(summary["active_deployed_to_prophet_grid"]) == routed
    assert position["has_position"] is True
    assert int(position["yes_shares_recorded"]) == yes_leg
    assert int(position["no_shares_recorded"]) == no_leg
    assert position["reconciled"] is False
    assert summary["checks"]["custody_partition_exact"] is True


def test_reconcile_neutral_restores_invariants_and_closes_position(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    routed, yes_leg, no_leg = _leg_split(state)
    install_grid(MockGrid(ratio_bps=10000))
    syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg, no_leg)
    report = payload(syndicate.reconcile_prophet_grid_position(1))
    summary = payload(syndicate.get_custody_accounting_summary())
    position = payload(syndicate.get_epoch_prophet_grid_position(1))
    assert int(report["reconciled_return_wei"]) == routed
    assert report["realized_gain_wei"] == "0" and report["realized_loss_wei"] == "0"
    assert position["reconciled"] is True and position["deployment_status"] == "RECONCILED_LIVE"
    assert int(summary["active_deployed_to_prophet_grid"]) == 0
    assert int(summary["reconciled_proceeds_total"]) == routed
    assert summary["ok"] is True


def test_reconcile_records_market_making_gain(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    routed, yes_leg, no_leg = _leg_split(state)
    pool_before = int(payload(syndicate.get_custody_accounting_summary())["partition"]["yield_pool_wei"])
    install_grid(MockGrid(ratio_bps=12500))
    syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg, no_leg)
    report = payload(syndicate.reconcile_prophet_grid_position(1))
    summary = payload(syndicate.get_custody_accounting_summary())
    gain = routed // 4
    assert int(report["realized_gain_wei"]) == gain
    assert report["realized_loss_wei"] == "0"
    assert int(summary["partition"]["yield_pool_wei"]) == pool_before + gain
    assert summary["ok"] is True and summary["checks"]["custody_partition_exact"] is True


def test_reconcile_junior_absorbs_first_loss_and_senior_is_protected(direct_vm, syndicate, direct_alice, direct_bob):
    funded(direct_vm, syndicate, direct_alice, 4 * GEN, 2 * GEN, direct_bob)
    mock_feeds(direct_vm, macro("18.53"), DEEP_BOOK)
    at(direct_vm, "2026-11-01T12:11:00Z")
    syndicate.execute_consensus_rebalance(1)
    state = state_for(syndicate, 1)
    routed, yes_leg, no_leg = _leg_split(state)
    senior_before = int(payload(syndicate.get_custody_accounting_summary())["partition"]["senior_principal_wei"])
    install_grid(MockGrid(ratio_bps=9000))
    syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg, no_leg)
    report = payload(syndicate.reconcile_prophet_grid_position(1))
    summary = payload(syndicate.get_custody_accounting_summary())
    loss = routed - routed * 9000 // 10000
    assert int(report["realized_loss_wei"]) == loss
    assert report["realized_gain_wei"] == "0"
    assert int(summary["partition"]["senior_principal_wei"]) == senior_before
    assert int(summary["partition"]["junior_principal_wei"]) < 2 * GEN - 2 * GEN * FEE_BPS // 10000
    assert summary["ok"] is True
    assert summary["checks"]["pst_backed_by_custody"] is True


def test_deploy_refuses_legs_that_do_not_sum_to_the_routed_amount(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    routed, yes_leg, no_leg = _leg_split(state)
    install_grid(MockGrid())
    try:
        syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg + 1, no_leg)
        raise AssertionError("legs must equal the routed deployment exactly")
    except Exception as exc:
        assert "equal the routed deployment" in str(exc)


def test_deploy_refuses_a_non_finalized_epoch(direct_vm, syndicate, direct_alice):
    opened(direct_vm, syndicate, direct_alice)
    direct_vm.value = GEN
    split_receipt(syndicate.subscribe_senior_tranche_payable())
    install_grid(MockGrid())
    try:
        syndicate.deploy_routed_capital_to_prophet_grid(1, "5", GEN // 2, GEN - GEN // 2)
        raise AssertionError("an unaudited epoch must not deploy")
    except Exception as exc:
        assert "finalized" in str(exc)


def test_deploy_is_idempotent_guarded(direct_vm, syndicate, direct_alice):
    state = audited(direct_vm, syndicate, direct_alice)
    routed, yes_leg, no_leg = _leg_split(state)
    install_grid(MockGrid())
    syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg, no_leg)
    try:
        syndicate.deploy_routed_capital_to_prophet_grid(1, "5", yes_leg, no_leg)
        raise AssertionError("a deployed epoch must not deploy twice")
    except Exception as exc:
        assert "already has a ProphetGrid position" in str(exc)


def test_reconcile_refuses_a_position_that_is_not_deployed(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    install_grid(MockGrid())
    try:
        syndicate.reconcile_prophet_grid_position(1)
        raise AssertionError("reconcile needs a deployed position")
    except Exception as exc:
        assert "no ProphetGrid position" in str(exc)


def test_reconcile_cannot_run_twice(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    deploy_and_reconcile(syndicate, 1)
    install_grid(MockGrid())
    try:
        syndicate.reconcile_prophet_grid_position(1)
        raise AssertionError("a reconciled position must not reconcile twice")
    except Exception as exc:
        assert "not awaiting reconciliation" in str(exc)


def test_view_exposes_active_deployed_and_reconciled_proceeds(direct_vm, syndicate, direct_alice):
    audited(direct_vm, syndicate, direct_alice)
    deploy_and_reconcile(syndicate, 1)
    summary = payload(syndicate.get_custody_accounting_summary())
    assert "active_deployed_to_prophet_grid" in summary
    assert "reconciled_proceeds_total" in summary
    assert int(summary["reconciled_proceeds_total"]) > 0
    position = payload(syndicate.get_epoch_prophet_grid_position(1))
    assert position["target_market_id"] == "5"
    assert int(position["reconciled_return_wei"]) > 0
