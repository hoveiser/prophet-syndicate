export const CONTRACT_ADDRESS: `0x${string}` =
  (import.meta.env.VITE_CONTRACT_ADDRESS as `0x${string}` | undefined) ??
  "0x0F9e290348e1500fA0800DDB89e5d770723038ef";

export const RPC_URL = "https://studio.genlayer.com/api";
export const CHAIN_ID = 61999;
export const NETWORK_LABEL = "STUDIONET";
export const EXPLORER_URL = `https://genlayer-explorer.vercel.app/contracts/${CONTRACT_ADDRESS}`;
export const REPO_URL = "https://github.com/hoveiser/prophet-syndicate";
export const LINKED_PROPHET_GRID_ADDRESS = "0x31C53838fF49168128D772625e34d3D2F7e11116";
export const LINKED_PROPHET_GRID_URL = "https://github.com/hoveiser/prophet-grid";
export const RUNNER_PIN = "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6";
export const RELEASE_TAG = "studionet-v2";

export const GEN = 1_000_000_000_000_000_000n;
export const FULL_SCALE_BPS = 10_000n;
export const GRID_STEP_BPS = 250n;

export const BANDS = [
  "HIGH_CONVICTION_DEPLOYMENT",
  "BALANCED_HEDGE_ALLOCATION",
  "DEFENSIVE_COLLATERAL_BUFFER",
  "CORRELATION_FREEZE_WITHDRAWAL",
] as const;

export type Band = (typeof BANDS)[number];

/** Mirrors _derive_routing_fields in contracts/prophet_syndicate.py, integer only. */
export const BAND_MODEL: Record<Band, { share: number; floor: number; bonusCap: number; juniorBase: number }> = {
  HIGH_CONVICTION_DEPLOYMENT: { share: 8000, floor: 250, bonusCap: 250, juniorBase: 2000 },
  BALANCED_HEDGE_ALLOCATION: { share: 5000, floor: 500, bonusCap: 250, juniorBase: 1250 },
  DEFENSIVE_COLLATERAL_BUFFER: { share: 2000, floor: 1000, bonusCap: 500, juniorBase: 250 },
  CORRELATION_FREEZE_WITHDRAWAL: { share: 0, floor: 0, bonusCap: 0, juniorBase: 0 },
};

export const TAIL_CEILINGS: [number, number, number] = [2000, 5000, 7500];

export const BAND_SHORT: Record<string, string> = {
  HIGH_CONVICTION_DEPLOYMENT: "HIGH CONVICTION",
  BALANCED_HEDGE_ALLOCATION: "BALANCED HEDGE",
  DEFENSIVE_COLLATERAL_BUFFER: "DEFENSIVE SHIELD",
  CORRELATION_FREEZE_WITHDRAWAL: "CORRELATION FREEZE",
  "": "AWAITING CONSENSUS",
};

export const PHASE_LABEL: Record<string, string> = {
  COLLECTING_SUBSCRIPTIONS: "INTAKE",
  AWAITING_CONSENSUS_AUDIT: "AUDIT",
  CHALLENGE_WINDOW_OPEN: "CHALLENGE",
  READY_TO_SETTLE: "SETTLABLE",
  PRINCIPAL_REFUND_OPEN: "REFUNDABLE",
  RELEASED: "RELEASED",
};
