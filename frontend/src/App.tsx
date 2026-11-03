import { useCallback, useEffect, useMemo, useState } from "react";
import { CONTRACT_ADDRESS, GEN, NETWORK_LABEL } from "./config";
import { burnerKey, connectInjected, deskFromKey, describeFailure, nativeBalanceOfVault, readJson, readOnlyDesk, send, type DeskHandle } from "./lib/chain";
import { shortAddress, wei } from "./lib/model";
import type { MarketPosition, Position, Summary } from "./types";
import { AuditConsole } from "./ui/AuditConsole";
import { BridgeWaterfall } from "./ui/BridgeWaterfall";
import { PositionLedger } from "./ui/PositionLedger";
import { StressModeler } from "./ui/StressModeler";
import { WalletBar } from "./ui/WalletBar";

interface Feed {
  summary: Summary | null;
  position: Position | null;
  positions: Record<string, MarketPosition>;
  chainVaultWei: bigint | null;
  deskWei: bigint | null;
}

export default function App() {
  const [slot, setSlot] = useState(0);
  const [desk, setDesk] = useState<DeskHandle | null>(null);
  const [feed, setFeed] = useState<Feed>({ summary: null, position: null, positions: {}, chainVaultWei: null, deskWei: null });
  const [busy, setBusy] = useState<string>("");
  const [notice, setNotice] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [clock, setClock] = useState(Math.floor(Date.now() / 1000));

  const attach = useCallback((handle: DeskHandle, label: string) => {
    setDesk(handle);
    setNotice({ tone: "ok", text: `${label} connected as ${shortAddress(handle.address)}` });
  }, []);

  const loadBurner = useCallback(
    (index: number) => {
      setSlot(index);
      attach(deskFromKey(burnerKey(index)), `burner desk ${index + 1}`);
    },
    [attach],
  );

  const refresh = useCallback(async () => {
    const probe = readOnlyDesk();
    const [summary, chainVaultWei] = await Promise.all([
      readJson<Summary>(probe.client, "get_custody_accounting_summary"),
      nativeBalanceOfVault(),
    ]);
    const account = desk?.address ?? probe.address;
    const position = await readJson<Position>(probe.client, "get_tranche_position", [account]);
    const routed = (summary.epochs ?? []).filter((row) => BigInt(row.derived_active_deployment_amount || "0") > 0n);
    const entries = await Promise.all(
      routed.map(async (row) => {
        const value = await readJson<MarketPosition>(probe.client, "get_epoch_prophet_grid_position", [Number(row.epoch_id)]);
        return [row.epoch_id, value] as const;
      }),
    );
    const positions: Record<string, MarketPosition> = {};
    for (const [id, value] of entries) positions[id] = value;
    const deskWei = desk?.writable ? await probe.client.getBalance({ address: desk.address as `0x${string}` }) : null;
    setFeed({ summary, position, positions, chainVaultWei, deskWei: typeof deskWei === "bigint" ? deskWei : wei(deskWei ?? 0n) });
    setClock(Math.floor(Date.now() / 1000));
  }, [desk]);

  useEffect(() => {
    void refresh().catch((error) => setNotice({ tone: "bad", text: describeFailure(error) }));
  }, [refresh]);

  useEffect(() => {
    const tick = window.setInterval(() => setClock((value) => value + 1), 1000);
    return () => window.clearInterval(tick);
  }, []);

  const act = useCallback(
    async (label: string, functionName: string, args: unknown[] = [], valueWei: bigint = 0n) => {
      if (!desk?.writable) {
        setNotice({ tone: "bad", text: "Select a burner desk or connect a wallet before sending" });
        return;
      }
      setBusy(`${label} waiting for validator consensus`);
      try {
        const outcome = await send(desk.client, functionName, args, valueWei);
        setNotice({
          tone: "ok",
          text: `${label} finalized with ${outcome.execution ?? "SUCCESS"} in ${outcome.hash.slice(0, 14)}...${outcome.hash.slice(-6)}`,
        });
      } catch (error) {
        setNotice({ tone: "bad", text: `${label}: ${describeFailure(error)}` });
      } finally {
        setBusy("");
        await refresh().catch(() => undefined);
      }
    },
    [desk, refresh],
  );

  const summary = feed.summary;
  const epochs = useMemo(() => summary?.epochs ?? [], [summary]);
  const intakeEpoch = epochs.find((row) => row.phase === "COLLECTING_SUBSCRIPTIONS") ?? null;

  return (
    <div className="min-h-screen bg-obsidian desk-grid">
      <div className="mx-auto max-w-[1440px] px-5 pb-16 pt-6">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-ridge pb-5">
          <div className="flex items-end gap-4">
            <img src="./logo.png" alt="" className="h-12 w-12 rounded border border-ridge" />
            <div>
              <div className="text-[10px] uppercase tracking-[0.4em] text-senior">ProphetGrid ecosystem / module 01</div>
              <h1 className="font-display text-2xl font-extrabold tracking-tight text-white">
                ProphetSyndicate<span className="text-senior">.</span>tranche desk
              </h1>
              <div className="mono mt-1 text-[11px] text-mute">
                {NETWORK_LABEL} chain 61999 / vault {shortAddress(CONTRACT_ADDRESS)} / runner 1jb45aa8
              </div>
            </div>
          </div>
          <WalletBar
            desk={desk}
            slot={slot}
            burnerLabel={shortAddress(deskFromKey(burnerKey(slot)).address)}
            deskBalance={feed.deskWei}
            onBurner={loadBurner}
            onInjected={() => {
              connectInjected()
                .then((handle) => attach(handle, "injected wallet"))
                .catch((error) => setNotice({ tone: "bad", text: describeFailure(error) }));
            }}
            onDetach={() => {
              setDesk(null);
              setNotice({ tone: "ok", text: "detached, the desk is read only" });
            }}
          />
        </header>

        <div className="mb-5 flex flex-wrap items-center gap-3">
          <button className="action-ghost" onClick={() => void refresh()} disabled={Boolean(busy)}>
            resync vault
          </button>
          <span className="tag">vault clock {new Date(clock * 1000).toISOString().slice(11, 19)}Z</span>
          {summary ? (
            <span className={`tag ${summary.ok ? "text-senior border-senior/40" : "text-rose border-rose/40"}`}>
              custody invariants {summary.ok ? "intact" : "broken"}
            </span>
          ) : null}
          {busy ? <span className="tag text-rebalance border-rebalance/40">{busy}</span> : null}
          {notice ? (
            <span className={`tag ${notice.tone === "ok" ? "text-senior border-senior/40" : "text-rose border-rose/40"}`}>
              {notice.text}
            </span>
          ) : null}
        </div>

        <div className="grid gap-5 xl:grid-cols-[1.15fr_0.85fr]">
          <BridgeWaterfall summary={summary} position={feed.position} chainVaultWei={feed.chainVaultWei} busy={busy} onAct={act} />
          <StressModeler summary={summary} intakeEpoch={intakeEpoch} />
        </div>

        <div className="mt-5">
          <PositionLedger
            summary={summary}
            positions={feed.positions}
            writable={Boolean(desk?.writable)}
            busy={busy}
            onAct={act}
          />
        </div>

        <div className="mt-5">
          <AuditConsole
            summary={summary}
            desk={desk}
            busy={busy}
            intakeEpoch={intakeEpoch}
            onAct={act}
            minSubscription={wei(summary?.limits?.min_subscription_wei ?? "1000000000000000")}
            routingBond={wei(summary?.limits?.routing_bond_wei ?? String(GEN / 100n))}
          />
        </div>
      </div>
    </div>
  );
}
