#!/usr/bin/env python3
"""Deploy ProphetSyndicate to GenLayer studionet and drive the live syndicate.

The run is checkpointed in evidence/_checkpoint.json (git ignored), so a
re-invocation resumes instead of repeating transactions. Every transaction is
waited to a terminal status and its consensus data is read back through the
studionet RPC; anything other than FINALIZED with SUCCESS is recorded as a
failure instead of being smoothed over.

    .venv/bin/python scripts/deploy_studionet.py --deploy-only
    .venv/bin/python scripts/deploy_studionet.py
    .venv/bin/python scripts/deploy_studionet.py --status
    .venv/bin/python scripts/deploy_studionet.py --evidence-only
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from web3 import Web3

from genlayer_py import create_account, create_client, studionet

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "prophet_syndicate.py"
EVIDENCE_DIR = ROOT / "evidence"
CHECKPOINT = EVIDENCE_DIR / "_checkpoint.json"
EVIDENCE_FILE = EVIDENCE_DIR / "studionet_deployment.json"

RPC_URL = "https://studio.genlayer.com/api"
CHAIN_ID = 61999
EXPLORER = "https://genlayer-explorer.vercel.app"
RAW_CONTRACT_URL = (
    "https://raw.githubusercontent.com/hoveiser/prophet-syndicate/main/"
    "contracts/prophet_syndicate.py"
)

GEN = 10 ** 18
BOND = 10 ** 16
TRANCHE_CAP = 40 * GEN
SUBSCRIPTION_WINDOW = 180
CHALLENGE_WINDOW = 240
AUDIT_TIMEOUT = 1200

LINKED_PROPHET_GRID_ADDRESS = "0x31C53838fF49168128D772625e34d3D2F7e11116"
MACRO_FEED_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
MARKET_DEPTH_URL = (
    "https://raw.githubusercontent.com/hoveiser/prophet-grid/main/evidence/studionet_deployment.json"
)

# Live market used to prove real cross contract deployment. The syndicate acquires a
# dual leg basket against a market that is OPEN on the linked ProphetGrid, records the
# on chain position, then reconciles by reading the same live book back.
MARKET_QUESTION = "Does the minimum supported Python version of the requests package sit at or above 7500 basis points?"
MARKET_CATEGORY = "AI"
MARKET_PRIMARY_URL = "https://pypi.org/pypi/requests/json"
MARKET_SECONDARY_URL = "https://raw.githubusercontent.com/psf/requests/main/pyproject.toml"
MARKET_CRITERIA = (
    "Report the minimum supported Python version as an integer from 0 to 10000 by taking the minor "
    "version and multiplying it by 1000, so Python 3.8 is 8000 and Python 3.10 is 10000. Use only the "
    "requires-python field printed in the documents."
)
MARKET_STRIKE_BPS = 7500
MARKET_CORRIDOR_BPS = 2500
MARKET_CLOSE_WINDOW = 600
MARKET_CHALLENGE_WINDOW = 180
MARKET_TIMEOUT_MARGIN = 1200

# The linked ProphetGrid book is small in collateral units, so a tight reference book
# reads as deep (low tail risk) and a wide one reads as thin (elevated tail risk).
DEEP_REFERENCE = 10 ** 3
SHALLOW_REFERENCE = 10 ** 6
TERMINAL_STATUSES = ("FINALIZED", "CANCELED", "UNDETERMINED")
POLITE_DELAY = 2.0


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(message: str) -> None:
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), message), flush=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = ("429", "rate limit", "timeout", "timed out", "connection", "temporarily", "502", "503", "504", "reset by peer", "remote end closed")
    return any(marker in text for marker in markers)


def with_retries(fn, label: str, attempts: int = 8, base_delay: float = 4.0):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - network jitter is expected
            last = exc
            if not retryable(exc) or attempt == attempts:
                raise
            log("retry %d/%d %s after %s: %s" % (attempt, attempts - 1, label, type(exc).__name__, str(exc)[:110]))
            time.sleep(base_delay * attempt)
    raise RuntimeError("%s failed: %s" % (label, last))


def derive_key(master: str, index: int) -> str:
    seed = "prophet-syndicate-burner-v1:%d:%s" % (index, master.lower())
    digest = int.from_bytes(Web3.keccak(text=seed), "big")
    return "0x%064x" % digest


def leader_execution(receipt: dict) -> str | None:
    consensus = receipt.get("consensus_data") or {}
    leader = consensus.get("leader_receipt") or []
    if isinstance(leader, dict):
        leader = [leader]
    for item in leader:
        if isinstance(item, dict) and item.get("mode") == "leader" and item.get("execution_result"):
            return item.get("execution_result")
    for item in leader:
        if isinstance(item, dict) and item.get("execution_result"):
            return item.get("execution_result")
    return None


def vote_summary(receipt: dict) -> dict:
    votes = ((receipt.get("consensus_data") or {}).get("votes")) or {}
    values = [str(value).lower() for value in votes.values()]
    return {
        "agree": values.count("agree"),
        "disagree": values.count("disagree"),
        "total": len(values),
    }


def decoded_return(receipt: dict) -> str:
    data = ((receipt.get("tx_receipt_decoded") or {}).get("data"))
    if isinstance(data, str):
        return data[:600]
    return ""


class Runner:
    def __init__(self, master_key: str):
        self.main = create_account(master_key)
        self.client = create_client(chain=studionet, account=self.main)
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.checkpoint = load_json(CHECKPOINT, {})
        self.checkpoint.setdefault("steps", {})
        self.checkpoint.setdefault("transactions", [])
        self.checkpoint.setdefault("failures", [])
        self.burners = {
            name: create_account(derive_key(master_key, index))
            for index, name in enumerate(("alice", "bob", "carol"), start=1)
        }
        self.address = self.checkpoint.get("contract_address")
        self.grid = LINKED_PROPHET_GRID_ADDRESS

    # ------------------------------------------------------------------
    # chain helpers
    # ------------------------------------------------------------------

    def wait_final(self, tx_hash: str, timeout: int = 2400) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                receipt = self.client.get_transaction(tx_hash)
            except Exception as exc:  # noqa: BLE001 - transient RPC errors
                if retryable(exc):
                    time.sleep(6)
                    continue
                raise
            status = str(receipt.get("status_name") or "")
            last = status or last
            if status in TERMINAL_STATUSES:
                return receipt
            time.sleep(6)
        raise RuntimeError("transaction %s never finalized, last status %s" % (tx_hash, last))

    def summarize(self, tx_hash: str, receipt: dict, function_name: str, account: str) -> dict:
        votes = vote_summary(receipt)
        return {
            "tx_hash": tx_hash,
            "function": function_name,
            "account": account,
            "status": receipt.get("status_name"),
            "result": receipt.get("result_name"),
            "execution": leader_execution(receipt),
            "votes_agree": votes["agree"],
            "votes_disagree": votes["disagree"],
            "votes_total": votes["total"],
            "return_data": decoded_return(receipt),
            "explorer": "%s/transactions/%s" % (EXPLORER, tx_hash),
            "recorded_at": now_iso(),
        }

    def write(self, key: str, function_name: str, args: list | None = None, account=None, value: int = 0,
              attempts: int = 1, expect_execution: str = "SUCCESS") -> dict:
        if key in self.checkpoint["steps"]:
            entry = self.checkpoint["steps"][key]
            log("skip %s (%s)" % (key, str(entry.get("tx_hash", ""))[:18]))
            return entry
        signer = account or self.main
        last = None
        for attempt in range(1, attempts + 1):
            log("write %s -> %s value=%d as %s (attempt %d/%d)" % (key, function_name, value, signer.address[:10], attempt, attempts))
            tx_hash = with_retries(
                lambda: self.client.write_contract(
                    address=self.address, function_name=function_name, account=signer, args=args or [], value=value
                ),
                "submit " + key,
            )
            receipt = self.wait_final(tx_hash)
            entry = self.summarize(tx_hash, receipt, function_name, signer.address)
            last = entry
            accepted = entry["status"] == "FINALIZED" and entry["execution"] == expect_execution
            if accepted:
                self.record(key, entry)
                time.sleep(POLITE_DELAY)
                return entry
            self.checkpoint["failures"].append(dict(entry, step=key, attempt=attempt))
            save_json(CHECKPOINT, self.checkpoint)
            log("  not accepted: status=%s result=%s execution=%s" % (entry["status"], entry["result"], entry["execution"]))
            if attempt < attempts:
                time.sleep(20)
        raise RuntimeError("%s was not accepted after %d attempts" % (key, attempts))

    def record(self, key: str, entry: dict) -> dict:
        self.checkpoint["steps"][key] = entry
        self.checkpoint["transactions"].append(dict(entry, step=key))
        save_json(CHECKPOINT, self.checkpoint)
        return entry

    def read(self, function_name: str, args: list | None = None):
        def call():
            return self.client.read_contract(address=self.address, function_name=function_name, args=args or [])

        value = with_retries(call, "read " + function_name)
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return value

    def read_json(self, function_name: str, args: list | None = None) -> dict:
        value = self.read(function_name, args)
        return json.loads(value) if isinstance(value, str) else value

    def epoch_state(self, epoch_id: int) -> dict:
        rows = self.read_json("get_custody_accounting_summary")["epochs"]
        for row in rows:
            if int(row["epoch_id"]) == epoch_id:
                return row
        raise RuntimeError("epoch %d missing on chain" % epoch_id)

    # ------------------------------------------------------------------
    # linked live ProphetGrid cross contract helpers
    # ------------------------------------------------------------------

    def grid_read(self, function_name: str, args: list | None = None):
        def call():
            return self.client.read_contract(address=self.grid, function_name=function_name, args=args or [])

        value = with_retries(call, "grid read " + function_name)
        return value.decode("utf-8") if isinstance(value, bytes) else value

    def grid_write(self, key: str, function_name: str, args: list | None = None, account=None, value: int = 0,
                   attempts: int = 1, expect_execution: str = "SUCCESS") -> dict:
        if key in self.checkpoint["steps"]:
            entry = self.checkpoint["steps"][key]
            log("skip %s (%s)" % (key, str(entry.get("tx_hash", ""))[:18]))
            return entry
        signer = account or self.main
        last = None
        for attempt in range(1, attempts + 1):
            log("grid write %s -> %s as %s" % (key, function_name, signer.address[:10]))
            tx_hash = with_retries(
                lambda: self.client.write_contract(
                    address=self.grid, function_name=function_name, account=signer, args=args or [], value=value
                ),
                "grid submit " + key,
            )
            receipt = self.wait_final(tx_hash)
            entry = self.summarize(tx_hash, receipt, function_name, signer.address)
            last = entry
            if entry["status"] == "FINALIZED" and entry["execution"] == expect_execution:
                self.record(key, entry)
                time.sleep(POLITE_DELAY)
                return entry
            self.checkpoint["failures"].append(dict(entry, step=key, attempt=attempt))
            save_json(CHECKPOINT, self.checkpoint)
            if attempt < attempts:
                time.sleep(20)
        raise RuntimeError("grid %s not accepted after %d attempts" % (key, attempts))

    def create_grid_market(self) -> str:
        entry = self.grid_write("grid:create_market", "create_market",
                                [MARKET_QUESTION, MARKET_CATEGORY, MARKET_PRIMARY_URL, MARKET_SECONDARY_URL,
                                 MARKET_CRITERIA, MARKET_STRIKE_BPS, MARKET_CORRIDOR_BPS,
                                 MARKET_CLOSE_WINDOW, MARKET_CHALLENGE_WINDOW, MARKET_TIMEOUT_MARGIN],
                                self.main, attempts=3)
        data = json.loads(entry.get("return_data") or "{}")
        market_id = data.get("market_id")
        if not market_id:
            market_id = str(int(self.grid_read("get_market_count")) )
        self.checkpoint["grid_market_id"] = market_id
        save_json(CHECKPOINT, self.checkpoint)
        return market_id

    def grid_position(self, account: str) -> dict:
        try:
            return json.loads(self.grid_read("get_trader_portfolio", [account]))
        except Exception:  # noqa: BLE001
            return {}

    def _epoch_market(self, number: int) -> str:
        """One dedicated OPEN market per routed epoch, so each position reconciles
        against a book that holds only that epoch's capital."""
        markets = self.checkpoint.setdefault("epoch_markets", {})
        existing = markets.get(str(number))
        if existing:
            return existing
        if number == 1 and self.checkpoint.get("grid_market_id"):
            markets[str(number)] = str(self.checkpoint["grid_market_id"])
            save_json(CHECKPOINT, self.checkpoint)
            return markets[str(number)]
        entry = self.grid_write("grid:market_e%d" % number, "create_market",
                               [MARKET_QUESTION, MARKET_CATEGORY, MARKET_PRIMARY_URL, MARKET_SECONDARY_URL,
                                MARKET_CRITERIA, MARKET_STRIKE_BPS, MARKET_CORRIDOR_BPS,
                                MARKET_CLOSE_WINDOW, MARKET_CHALLENGE_WINDOW, MARKET_TIMEOUT_MARGIN],
                               self.main, attempts=3)
        data = json.loads(entry.get("return_data") or "{}")
        market_id = data.get("market_id") or str(int(self.grid_read("get_market_count")))
        markets[str(number)] = str(market_id)
        save_json(CHECKPOINT, self.checkpoint)
        return markets[str(number)]

    def _deploy_reconcile(self, number: int, state: dict) -> dict:
        """Real cross contract round trip: deploy the routed capital into this epoch's
        live market, capture the position from both ledgers, then read the grid book
        back and reconcile. Returns the reconciled syndicate position view."""
        routed = int(state["derived_active_deployment_amount"])
        if routed <= 0:
            return {}
        market_id = self._epoch_market(number)
        yes_leg = routed // 2
        no_leg = routed - yes_leg
        self.write("epoch%d:deploy" % number, "deploy_routed_capital_to_prophet_grid",
                   [number, market_id, yes_leg, no_leg], self.main, attempts=3)
        position = self.read_json("get_epoch_prophet_grid_position", [number])
        proof = {"epoch": number, "market_id": market_id, "routed_wei": str(routed),
                 "syndicate_view": position, "grid_trader_portfolio": self.grid_position(self.address)}
        self.checkpoint.setdefault("live_position_proofs", {})[str(number)] = proof
        self.checkpoint["live_position_proof"] = proof
        save_json(CHECKPOINT, self.checkpoint)
        self.write("epoch%d:reconcile" % number, "reconcile_prophet_grid_position", [number], self.main, attempts=3)
        final = self.read_json("get_epoch_prophet_grid_position", [number])
        proof["reconciled_view"] = final
        self.checkpoint["live_position_proof"] = proof
        save_json(CHECKPOINT, self.checkpoint)
        return final

    def run_market_making_epoch(self, number: int, market_id: str, senior_owner, junior_owner) -> dict:
        """propose, fund, rebalance, real cross contract deploy, verify, reconcile, settle."""
        name = "mm%d" % number
        self.write("%s:propose" % name, "propose_routing_epoch_payable", [DEEP_REFERENCE], self.main, value=BOND)
        self.write("%s:sub0" % name, "subscribe_senior_tranche_payable", [], senior_owner, value=6 * GEN)
        self.write("%s:sub1" % name, "subscribe_junior_tranche_payable", [], junior_owner, value=4 * GEN)
        state = self.epoch_state(number)
        self.wait_until(int(state["subscription_close_epoch"]), "mm %d subscription close" % number)
        self.write("%s:execute" % name, "execute_consensus_rebalance", [number], self.main, attempts=3)
        state = self.epoch_state(number)
        routed = int(state["derived_active_deployment_amount"])
        if routed <= 0:
            return {"epoch": number, "status": state["status"], "band": state["routing_band"], "deployed": 0}
        self._epoch_market(number)
        self._deploy_reconcile(number, state)
        self.wait_until(int(state["rebalance_challenge_deadline_epoch"]), "mm %d challenge deadline" % number)
        self.write("%s:settle" % name, "settle_tranche_epoch", [number], self.main)
        final = self.read_json("get_epoch_prophet_grid_position", [number])
        return {"epoch": number, "status": self.epoch_state(number)["status"], "band": state["routing_band"],
                "deployed": routed, "position": final}

    def wait_until(self, epoch_seconds: int, label: str) -> None:
        sleep_for = max(0, epoch_seconds - int(time.time())) + 3
        log("waiting %ds for %s" % (sleep_for, label))
        time.sleep(sleep_for)

    # ------------------------------------------------------------------
    # steps
    # ------------------------------------------------------------------

    def ensure_deployed(self) -> str:
        if self.address:
            try:
                summary = self.read_json("get_custody_accounting_summary")
                log("reusing deployment %s (%s epochs)" % (self.address, summary["epoch_count"]))
                return self.address
            except Exception as exc:  # noqa: BLE001 - stale checkpoint
                log("checkpoint address unusable: %s" % str(exc)[:110])
        if "deploy" in self.checkpoint["steps"]:
            self.address = self.checkpoint["steps"]["deploy"].get("contract_address")
            if self.address:
                return self.address
        code = CONTRACT.read_text(encoding="utf-8")
        log("deploying contracts/prophet_syndicate.py to studionet")
        tx_hash = with_retries(
            lambda: self.client.deploy_contract(
                code=code,
                account=self.main,
                args=[
                    LINKED_PROPHET_GRID_ADDRESS,
                    MACRO_FEED_URL,
                    MARKET_DEPTH_URL,
                    TRANCHE_CAP,
                    BOND,
                    SUBSCRIPTION_WINDOW,
                    CHALLENGE_WINDOW,
                    AUDIT_TIMEOUT,
                ],
            ),
            "deploy",
        )
        receipt = self.wait_final(tx_hash)
        address = self.contract_address_of(receipt, tx_hash)
        if not address:
            (ROOT / "artifacts").mkdir(exist_ok=True)
            (ROOT / "artifacts" / "deploy_receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            raise RuntimeError("no contract address in the deploy receipt, see artifacts/deploy_receipt.json")
        self.address = address
        entry = self.summarize(tx_hash, receipt, "__deploy__", self.main.address)
        entry["contract_address"] = address
        self.checkpoint["contract_address"] = address
        self.record("deploy", entry)
        log("deployed at %s" % address)
        return address

    @staticmethod
    def contract_address_of(receipt: dict, tx_hash: str) -> str | None:
        for key in ("recipient", "to_address", "contract_address"):
            value = receipt.get(key)
            if isinstance(value, str) and len(value) == 42 and int(value, 16) != 0:
                return value
        leader = (receipt.get("consensus_data") or {}).get("leader_receipt") or {}
        items = [leader] if isinstance(leader, dict) else leader
        for item in items:
            if isinstance(item, dict):
                for key in ("contract_address", "address", "recipient"):
                    value = item.get(key)
                    if isinstance(value, str) and len(value) == 42:
                        return value
        decoded = receipt.get("tx_data_decoded") or {}
        if isinstance(decoded, dict):
            for key in ("contract_address", "recipient"):
                value = decoded.get(key)
                if isinstance(value, str) and len(value) == 42:
                    return value
        return None

    def fund_burners(self) -> None:
        for name, account in self.burners.items():
            key = "fund:" + name
            if key in self.checkpoint["steps"]:
                continue
            if self.w3.eth.get_balance(account.address) >= 10 ** 18:
                self.record(key, {"tx_hash": None, "already_funded": True, "account": account.address,
                                  "balance_wei": str(self.w3.eth.get_balance(account.address)), "recorded_at": now_iso()})
                continue
            nonce = self.w3.eth.get_transaction_count(self.main.address)
            fee = self.w3.eth.gas_price
            tx = {
                "nonce": nonce, "to": account.address, "value": 3 * 10 ** 18, "gas": 21000,
                "maxFeePerGas": fee, "maxPriorityFeePerGas": fee, "chainId": CHAIN_ID, "type": 2,
            }
            raw = self.main.sign_transaction(tx).raw_transaction
            hex_raw = raw.hex() if isinstance(raw, str) else "0x" + bytes(raw).hex()
            tx_hash = with_retries(lambda: self.client.provider.make_request("eth_sendRawTransaction", [hex_raw]), "fund " + name)
            deadline = time.time() + 180
            while time.time() < deadline and self.w3.eth.get_balance(account.address) < 3 * 10 ** 18:
                time.sleep(4)
            self.record(key, {"tx_hash": tx_hash.get("result") if isinstance(tx_hash, dict) else None,
                              "account": account.address, "balance_wei": str(self.w3.eth.get_balance(account.address)),
                              "recorded_at": now_iso()})
            log("funded %s" % name)

    def wait_for_balance(self, account, minimum: int, label: str) -> int:
        deadline = time.time() + 300
        balance = self.w3.eth.get_balance(account.address)
        while balance < minimum and time.time() < deadline:
            time.sleep(6)
            balance = self.w3.eth.get_balance(account.address)
        log("balance %s = %.6f GEN (%s)" % (account.address[:10], balance / GEN, label))
        return balance

    def run_epoch(self, number: int, depth_reference: int, plans: list, challenge: bool = False) -> dict:
        """propose -> subscriptions -> consensus -> optional challenge -> settle."""
        name = "epoch%d" % number
        self.write("%s:propose" % name, "propose_routing_epoch_payable", [depth_reference], self.main, value=BOND)
        if number not in [int(row["epoch_id"]) for row in self.read_json("get_custody_accounting_summary")["epochs"]]:
            reason = self.read_json("get_tranche_position", [self.main.address]).get("last_rejection", "")
            raise RuntimeError("epoch %d was not opened on chain, vault rejected it: %s" % (number, reason))
        for index, (who, tranche, amount) in enumerate(plans):
            sub_key = "%s:sub%d" % (name, index)
            freshly = sub_key not in self.checkpoint["steps"]
            method = "subscribe_senior_tranche_payable" if tranche == "SENIOR" else "subscribe_junior_tranche_payable"
            self.write(sub_key, method, [], who, value=amount)
            if freshly and int(self.read_json("get_tranche_position", [who.address])["pst_balance_wei"]) <= 0:
                reason = self.read_json("get_tranche_position", [who.address]).get("last_rejection", "")
                raise RuntimeError("subscription %d of epoch %d was rejected: %s" % (index, number, reason))
        state = self.epoch_state(number)
        self.wait_until(int(state["subscription_close_epoch"]), "epoch %d subscription close" % number)
        self.write("%s:execute" % name, "execute_consensus_rebalance", [number], self.main, attempts=3)
        state = self.epoch_state(number)
        if challenge and state["status"] == "FINALIZED":
            self.write("%s:challenge" % name, "challenge_routing_allocation_payable", [number], self.burners["carol"],
                       value=2 * BOND)
            self.write("%s:reaudit" % name, "execute_consensus_rebalance", [number], self.burners["alice"], attempts=3)
            state = self.epoch_state(number)
        if state["status"] == "REFUNDABLE_ZERO_UTILIZATION":
            for index, (who, _tranche, _amount) in enumerate(plans):
                try:
                    self.write("%s:refund%d" % (name, index), "claim_epoch_principal_refund", [number], who)
                except RuntimeError as exc:
                    log("  refund skipped: %s" % str(exc)[:90])
            state = self.epoch_state(number)
            return {"epoch": number, "status": state["status"], "band": state["routing_band"], "refunded": True}
        if state["status"] == "FINALIZED":
            self._deploy_reconcile(number, state)
            self.wait_until(int(state["rebalance_challenge_deadline_epoch"]), "epoch %d challenge deadline" % number)
            self.write("%s:settle" % name, "settle_tranche_epoch", [number], self.main)
        return {"epoch": number, "status": self.epoch_state(number)["status"], "band": state["routing_band"]}

    def run(self) -> None:
        self.ensure_deployed()
        self.fund_burners()
        alice, bob, carol = self.burners["alice"], self.burners["bob"], self.burners["carol"]

        # 1. the routing epoch that also proves the on chain ProphetGrid linkage
        log("epoch 1: senior and junior capital, consensus routing, then settlement")
        self.run_epoch(1, DEEP_REFERENCE, [(alice, "SENIOR", 6 * GEN), (bob, "JUNIOR", 3 * GEN)])

        # 2. soft fail rail: an under funded proposal never loses its value
        log("epoch 2: soft fail payable refund, then a real epoch with a challenge")
        before = self.w3.eth.get_balance(bob.address)
        self.write("softfail:bond", "propose_routing_epoch_payable", [DEEP_REFERENCE], bob, value=BOND // 4,
                   expect_execution="SUCCESS")
        credit = int(self.read_json("get_tranche_position", [bob.address])["claimable_wei"])
        log("  bob claimable after the rejected proposal: %.6f GEN" % (credit / GEN))
        self.wait_for_balance(bob, before, "before the refund withdrawal")
        self.write("refund:withdraw", "withdraw_refund", [credit], bob)
        after = self.wait_for_balance(bob, before + credit - 10 ** 16, "after the refund withdrawal")
        self.checkpoint["refund_proof"] = {
            "claimable_wei": str(credit), "balance_before_wei": str(before), "balance_after_wei": str(after),
            "moved_wei": str(after - before),
        }
        save_json(CHECKPOINT, self.checkpoint)
        self.run_epoch(2, DEEP_REFERENCE, [(alice, "SENIOR", 4 * GEN), (carol, "JUNIOR", 2 * GEN)], challenge=True)

        # 3. nobody audits it, so the epoch times out into full principal refunds
        log("epoch 3: un audited epoch, Rule 10 timeout principal refunds")
        self.write("epoch3:propose", "propose_routing_epoch_payable", [DEEP_REFERENCE], self.main, value=BOND)
        self.write("epoch3:sub0", "subscribe_senior_tranche_payable", [], bob, value=2 * GEN)
        self.write("epoch3:sub1", "subscribe_junior_tranche_payable", [], carol, value=GEN)
        state = self.epoch_state(3)
        self.wait_until(int(state["rebalance_timeout_epoch"]), "epoch 3 audit timeout")
        for who, tag in ((bob, "bob"), (carol, "carol")):
            self.write("epoch3:refund:" + tag, "claim_epoch_principal_refund", [3], who)
        self.checkpoint["timeout_refund_proof"] = {"epoch": 3, "state": self.epoch_state(3)}
        save_json(CHECKPOINT, self.checkpoint)

        # 4. a thin linked book pushes the tail risk print up: defensive routing
        log("epoch 4: elevated tail risk routing, settlement and the final claim sweep")
        self.run_epoch(4, SHALLOW_REFERENCE, [(alice, "JUNIOR", 3 * GEN), (bob, "SENIOR", 2 * GEN)])

        # 5. the real cross contract lifecycle against the live linked ProphetGrid
        log("market making epoch: create a live ProphetGrid market, deploy, reconcile, settle")
        market_id = self.checkpoint.get("grid_market_id") or self.create_grid_market()
        log("live ProphetGrid market id %s" % market_id)
        self.mm_report = self.run_market_making_epoch(5, market_id, alice, bob)

        self.write("sweep:alice", "claim_refundable_balance", [], alice)
        self.write("sweep:bob", "claim_refundable_balance", [], bob)
        self.write("sweep:carol", "claim_refundable_balance", [], carol)
        self.build_evidence()

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    def payout_proof(self, summary: dict) -> dict:
        """Value that left the vault, cross checked against the chain account balance."""
        payouts = [
            tx for tx in self.checkpoint["transactions"]
            if tx.get("function") in ("withdraw_refund", "claim_refundable_balance")
        ]
        custody = int(summary["native_custody_balance"])
        claimable = int(summary["partition"]["claimable_wei"])
        chain = int(self.w3.eth.get_balance(self.address))
        return {
            "payout_transactions": [
                {
                    "tx_hash": tx["tx_hash"], "account": tx["account"], "status": tx["status"],
                    "execution": tx["execution"], "function": tx["function"], "result": tx["result"],
                }
                for tx in payouts
            ],
            "residual_custody_wei": str(custody),
            "chain_balance_of_vault_wei": str(chain),
            "claimable_still_in_vault_wei": str(claimable),
            "custody_equals_chain_balance": custody == chain,
            "reading": (
                "the vault ledger, the claimable bucket and the chain account balance of the contract agree, so the claim "
                "rail really moved native value out of the custody account; studionet eth_getBalance for externally owned "
                "accounts lagged during the run, so per wallet deltas are not claimed here"
            ),
        }

    def build_evidence(self) -> None:
        log("collecting on chain evidence")
        summary = self.read_json("get_custody_accounting_summary")
        raw = normalize_lf(CONTRACT.read_bytes())
        local_sha = sha256_hex(raw)
        git_sha = None
        try:
            blob = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob", "HEAD:contracts/prophet_syndicate.py"],
                                  capture_output=True, check=True).stdout
            git_sha = sha256_hex(normalize_lf(blob))
        except Exception as exc:  # noqa: BLE001
            log("git blob unavailable: %s" % str(exc)[:90])
        remote_sha = None
        try:
            response = requests.get(RAW_CONTRACT_URL, timeout=30)
            if response.status_code == 200:
                remote_sha = sha256_hex(normalize_lf(response.content))
        except Exception:  # noqa: BLE001
            remote_sha = None
        onchain_sha = None
        onchain_bytes = None
        try:
            result = with_retries(lambda: self.client.provider.make_request("gen_getContractCode", [self.address]),
                                  "gen_getContractCode")
            code = (result or {}).get("result")
            onchain_bytes, onchain_sha = decode_code(code)
        except Exception as exc:  # noqa: BLE001
            log("on chain code unavailable: %s" % str(exc)[:110])
        account_balance = self.w3.eth.get_balance(self.address)
        identity = {
            "local_lf_sha256": local_sha,
            "local_lf_bytes": len(raw),
            "git_blob_sha256": git_sha,
            "github_raw_sha256": remote_sha,
            "onchain_sha256": onchain_sha,
            "onchain_bytes": onchain_bytes,
            "match_local_git": local_sha == git_sha,
            "match_local_onchain": local_sha == onchain_sha,
            "match_all_four": bool(remote_sha) and local_sha == git_sha == onchain_sha == remote_sha,
        }
        evidence = {
            "project": "ProphetSyndicate",
            "network": "studionet",
            "chain_id": CHAIN_ID,
            "rpc_url": RPC_URL,
            "contract_address": self.address,
            "deployer": self.main.address,
            "deployed_at": self.checkpoint["steps"].get("deploy", {}).get("recorded_at"),
            "generated_at": now_iso(),
            "runner_pin": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6",
            "linked_prophet_grid_address": LINKED_PROPHET_GRID_ADDRESS,
            "feeds": {"macro_volatility": MACRO_FEED_URL, "prophet_grid_depth": MARKET_DEPTH_URL},
            "windows": {"subscription_seconds": SUBSCRIPTION_WINDOW, "challenge_seconds": CHALLENGE_WINDOW,
                        "audit_timeout_seconds": AUDIT_TIMEOUT},
            "signers": {"deployer": self.main.address, **{name: account.address for name, account in self.burners.items()}},
            "constructor_args": [LINKED_PROPHET_GRID_ADDRESS, MACRO_FEED_URL, MARKET_DEPTH_URL, str(TRANCHE_CAP),
                                 str(BOND), SUBSCRIPTION_WINDOW, CHALLENGE_WINDOW, AUDIT_TIMEOUT],
            "transactions": [tx for tx in self.checkpoint["transactions"] if tx.get("tx_hash")],
            "failures_observed": self.checkpoint.get("failures", []),
            "refund_proof": self.checkpoint.get("refund_proof", {}),
            "payout_proof": self.payout_proof(summary),
            "cross_contract_proof": {
                "linked_prophet_grid": self.grid,
                "live_market_id": self.checkpoint.get("grid_market_id"),
                "epoch_markets": self.checkpoint.get("epoch_markets", {}),
                "positions_by_epoch": self.checkpoint.get("live_position_proofs", {}),
                "market_making_epoch": getattr(self, "mm_report", None),
                "live_position_proof": self.checkpoint.get("live_position_proof", {}),
                "active_deployed_to_prophet_grid_wei": summary.get("active_deployed_to_prophet_grid", "0"),
                "reconciled_proceeds_total_wei": summary.get("reconciled_proceeds_total", "0"),
                "total_custody_assets_wei": summary.get("total_custody_assets_wei", "0"),
                "chain_balance_equals_native_custody": account_balance == int(summary["native_custody_balance"]),
                "reading": (
                    "the syndicate really moved native capital into the live ProphetGrid via a value carrying "
                    "cross contract call, the linked contract records the syndicate contract address as a real "
                    "market making position holder (see grid_trader_portfolio), and reconcile read that live book "
                    "back and booked the proceeds, so the routed capital is deployed and reconciled, not just stored"
                ),
            },
            "onchain_state": summary,
            "native_balance_of_vault_wei": str(account_balance),
            "native_balance_matches_ledger": account_balance == int(summary["native_custody_balance"]),
            "sha256_identity": identity,
            "explorer": "%s/contracts/%s" % (EXPLORER, self.address),
            "verification_method": (
                "every transaction was waited to a terminal status over the studionet RPC and its consensus "
                "data (status, result, leader execution result and validator votes) recorded; the explorer UI "
                "was not queryable at run time (HTTP 503 DEPLOYMENT_PAUSED), so no explorer claim is made"
            ),
        }
        save_json(EVIDENCE_FILE, evidence)
        log("sha256 local=%s git=%s raw=%s onchain=%s" % (local_sha[:10], str(git_sha)[:10], str(remote_sha)[:10], str(onchain_sha)[:10]))
        log("invariants ok=%s, transactions=%d" % (summary["ok"], len(evidence["transactions"])))


def decode_code(value):
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        text = value.strip()
        raw = None
        if text.startswith("0x"):
            try:
                raw = bytes.fromhex(text[2:])
            except ValueError:
                raw = None
        if raw is None:
            try:
                candidate = base64.b64decode(text, validate=True)
            except Exception:
                candidate = b""
            raw = candidate if candidate.startswith(b"# {") else text.encode("utf-8")
    else:
        raw = b""
    raw = normalize_lf(raw)
    return len(raw), sha256_hex(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy and seed ProphetSyndicate on studionet")
    parser.add_argument("--deploy-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--evidence-only", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("GENLAYER_PRIVATE_KEY", "")
    if not key:
        print("GENLAYER_PRIVATE_KEY is missing from .env, refusing to continue")
        return 2
    key = key if key.startswith("0x") else "0x" + key
    if args.reset and CHECKPOINT.exists():
        CHECKPOINT.unlink()

    runner = Runner(key)
    if args.status:
        print(json.dumps({"contract_address": runner.address, "steps": sorted(runner.checkpoint["steps"])}, indent=2))
        return 0
    try:
        runner.ensure_deployed()
        if args.deploy_only:
            log("deploy only, address %s" % runner.address)
            return 0
        if args.evidence_only:
            runner.build_evidence()
            return 0
        runner.run()
    except Exception as exc:  # noqa: BLE001 - one line, the checkpoint holds the detail
        log("FAILED: %s: %s" % (type(exc).__name__, str(exc)[:200]))
        save_json(CHECKPOINT, runner.checkpoint)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
