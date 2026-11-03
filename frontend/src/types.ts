export interface EpochState {
  epoch_id: string;
  status: string;
  phase: string;
  proposed_by?: string;
  subscription_close_epoch: string;
  rebalance_challenge_deadline_epoch: string;
  rebalance_timeout_epoch: string;
  routing_band: string;
  quantized_volatility_baseline_bps: string;
  quantized_tail_risk_bps: string;
  derived_active_deployment_amount: string;
  derived_senior_reserve_buffer: string;
  derived_senior_yield_bps: string;
  derived_junior_yield_bps: string;
  senior_commit_wei: string;
  junior_commit_wei: string;
  senior_floor_wei: string;
  challenge_count: string;
  prophet_grid_deployment_status?: string;
  prophet_grid_reconciled?: boolean;
}

export interface MarketPosition {
  epoch_id: string;
  has_position: boolean;
  deployment_status: string;
  target_market_id?: string;
  deployed_capital_wei?: string;
  yes_shares_recorded?: string;
  no_shares_recorded?: string;
  prophet_grid_entry_snapshot?: string;
  reconciled?: boolean;
  reconciled_return_wei?: string;
  realized_gain_wei?: string;
  realized_loss_wei?: string;
  active_deployed_to_prophet_grid: string;
  reconciled_proceeds_total: string;
}

export interface Summary {
  ok: boolean;
  checks: Record<string, boolean>;
  clock_epoch: string;
  epochs: EpochState[];
  partition: Record<string, string>;
  native_custody_balance: string;
  vault_custody_balance: string;
  active_deployed_to_prophet_grid: string;
  reconciled_proceeds_total: string;
  total_custody_assets_wei: string;
  pst_total_supply: string;
  vm_account_balance_wei: string;
  reserved_liability_wei: string;
  unfunded_floor_wei: string;
  payout_route?: string;
  soft_fail_count: string;
  linked_prophet_grid_address: string;
  linked_market_count: string;
  linked_probe_status: string;
  macro_feed_url: string;
  market_depth_url: string;
  active_epoch_id?: string;
  epoch_count: string;
  runner_pin: string;
  principle: string;
  limits: Record<string, string>;
}

export interface Position {
  account: string;
  senior_principal_wei: string;
  junior_principal_wei: string;
  pst_balance_wei: string;
  claimable_wei: string;
  locked_bond_wei: string;
  last_rejection: string;
  active_epoch: EpochState | Record<string, never>;
}

export type Desk = "IDLE" | "LOCAL" | "INJECTED";
