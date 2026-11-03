import { useState } from "react";
import { LINKED_PROPHET_GRID_ADDRESS } from "../config";
import { formatGen, wei } from "../lib/model";
import type { MarketPosition, Summary } from "../types";

type Act = (label: string, functionName: string, args?: unknown[], valueWei?: bigint) => Promise<void>;

interface Props {
  summary: Summary | null;
  positions: Record<string, MarketPosition>;
  writable: boolean;
  busy: string;
  onAct: Act;
}

const STATUS_TONE: Record<string, string> = {
  DEPLOYED_LIVE: "text-rebalance border-rebalance/40",
  RECONCILED_LIVE: "text-senior border-senior/40",
  UNDEPLOYED: "text-mute",
};

export function PositionLedger({ summary, positions, writable, busy, onAct }: Props) {
  const [marketId, setMarketId] = useState("5");
  const epochs = summary?.epochs ?? [];
  const routed = epochs.filter((row) => wei(row.derived_active_deployment_amount) > 0n);
  const deployed = wei(summary?.active_deployed_to_prophet_grid);
  const proceeds = wei(summary?.reconciled_proceeds_total);
  const assets = wei(summary?.total_custody_assets_wei);
  const bucketed = Object.values(summary?.partition ?? {}).reduce((total, value) => total + wei(value), 0n);

  return (
    <section className="pane">
      <div className="pane-head">
        <div>
          <div className="pane-title">live ProphetGrid position and reconciliation ledger</div>
          <div className="mono text-[11px] text-mute">
            real cross contract capital deployment into {LINKED_PROPHET_GRID_ADDRESS.slice(0, 10)}...{LINKED_PROPHET_GRID_ADDRESS.slice(-4)}
          </div>
        </div>
        <span className="tag">linked ProphetGrid</span>
      </div>

      <div className="grid gap-3 px-4 py-4 md:grid-cols-4">
        <Cell label="active deployed to ProphetGrid" value={`${formatGen(deployed, 4)} GEN`} tone="text-rebalance" />
        <Cell label="reconciled proceeds total" value={`${formatGen(proceeds, 4)} GEN`} tone="text-senior" />
        <Cell label="total custody assets" value={`${formatGen(assets, 4)} GEN`} tone="text-white" />
        <Cell label="accounting equation" value={bucketed === assets ? "exact" : "MISMATCH"} tone={bucketed === assets ? "text-senior" : "text-rose"} />
      </div>

      <div className="px-4 pb-4">
        <div className="mono mb-3 text-[11px] text-mute">
          native custody {formatGen(wei(summary?.native_custody_balance), 4)} GEN plus deployed plus reconciled proceeds equals the tranche and claim buckets
        </div>
        <div className="space-y-3">
          {routed.length === 0 ? (
            <div className="mono text-[12px] text-mute">no epoch has routed capital into ProphetGrid yet</div>
          ) : null}
          {routed.map((row) => {
            const position = positions[row.epoch_id];
            const hasPosition = Boolean(position?.has_position);
            const reconciled = Boolean(position?.reconciled);
            const routedWei = wei(row.derived_active_deployment_amount);
            const yesLeg = routedWei / 2n;
            const noLeg = routedWei - yesLeg;
            return (
              <article key={row.epoch_id} className="rounded border border-ridge bg-obsidian p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="mono text-[12px] text-white">epoch {row.epoch_id}</span>
                    <span className={`tag ${STATUS_TONE[position?.deployment_status ?? "UNDEPLOYED"] ?? ""}`}>
                      {position?.deployment_status ?? "UNDEPLOYED"}
                    </span>
                    {hasPosition ? <span className="tag">market {position?.target_market_id}</span> : null}
                  </div>
                  <div className="mono text-[11px] text-mute">routed {formatGen(row.derived_active_deployment_amount, 4)} GEN</div>
                </div>

                {hasPosition ? (
                  <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-4">
                    <Cell label="deployed capital" value={`${formatGen(position?.deployed_capital_wei ?? "0", 4)} GEN`} tone="text-white" />
                    <Cell label="YES shares held" value={position?.yes_shares_recorded ?? "0"} tone="text-junior" />
                    <Cell label="NO shares held" value={position?.no_shares_recorded ?? "0"} tone="text-senior" />
                    <Cell label="reconciled" value={reconciled ? "yes" : "pending"} tone={reconciled ? "text-senior" : "text-rebalance"} />
                    <Cell label="reconciled return" value={`${formatGen(position?.reconciled_return_wei ?? "0", 4)} GEN`} tone="text-white" />
                    <Cell label="realized gain" value={`${formatGen(position?.realized_gain_wei ?? "0", 4)} GEN`} tone="text-senior" />
                    <Cell label="realized loss" value={`${formatGen(position?.realized_loss_wei ?? "0", 4)} GEN`} tone="text-rose" />
                    <Cell label="PnL signed" value={`${signedPnl(position)} GEN`} tone="text-white" />
                  </div>
                ) : null}

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2">
                    <span className="label">market id</span>
                    <input
                      className="field w-20"
                      value={marketId}
                      onChange={(event) => setMarketId(event.target.value.replace(/[^0-9]/g, ""))}
                    />
                  </label>
                  {!hasPosition && row.status === "FINALIZED" ? (
                    <button
                      className="action-warn"
                      disabled={!writable || Boolean(busy) || !marketId}
                      onClick={() =>
                        void onAct("deploy to ProphetGrid", "deploy_routed_capital_to_prophet_grid", [
                          Number(row.epoch_id), marketId, yesLeg.toString(), noLeg.toString(),
                        ])
                      }
                    >
                      deploy {formatGen(yesLeg)} / {formatGen(noLeg)} GEN dual leg
                    </button>
                  ) : null}
                  {hasPosition && !reconciled ? (
                    <button
                      className="action"
                      disabled={!writable || Boolean(busy)}
                      onClick={() => void onAct("reconcile position", "reconcile_prophet_grid_position", [Number(row.epoch_id)])}
                    >
                      reconcile live position
                    </button>
                  ) : null}
                  {reconciled ? <span className="mono text-[11px] text-senior">position reconciled into the syndicate ledger</span> : null}
                </div>
              </article>
            );
          })}
        </div>
        <div className="mono mt-3 text-[11px] text-mute">
          deployment is a value carrying cross contract call, the linked contract records this desk as a live position holder, and reconcile redeems and rebalances the tranche ledger
        </div>
      </div>
    </section>
  );
}

function Cell({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`mono text-[12px] ${tone}`}>{value}</div>
    </div>
  );
}

function signedPnl(position?: MarketPosition): string {
  const gain = wei(position?.realized_gain_wei);
  const loss = wei(position?.realized_loss_wei);
  if (gain === loss) return "0.000";
  return gain > loss ? `+${formatGen(gain - loss)}` : `-${formatGen(loss - gain)}`;
}
