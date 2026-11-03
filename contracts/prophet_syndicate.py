# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""ProphetSyndicate: AI rebalanced tranche vault that really deploys routed capital into live ProphetGrid market making, tracks each position, and reconciles it before settlement."""

import json
from dataclasses import dataclass

from genlayer import *

RUNNER_PIN = "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6"
ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"
DEFAULT_LINKED_PROPHET_GRID_ADDRESS = "0xB0aeDBb6E8056c0a2c90E4BD901bB0f3DA6Bb967"
DEFAULT_MACRO_FEED_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
DEFAULT_MARKET_DEPTH_URL = "https://raw.githubusercontent.com/hoveiser/prophet-grid/main/evidence/studionet_deployment.json"
ENTRY_FEE_BPS = 25
BAND_HIGH, BAND_BALANCED = "HIGH_CONVICTION_DEPLOYMENT", "BALANCED_HEDGE_ALLOCATION"
BAND_DEFENSIVE, BAND_FREEZE = "DEFENSIVE_COLLATERAL_BUFFER", "CORRELATION_FREEZE_WITHDRAWAL"
TRANCHE_SENIOR, TRANCHE_JUNIOR = "SENIOR", "JUNIOR"
STATUS_OPEN = "SUBSCRIPTION_OPEN"
STATUS_FINALIZED = "FINALIZED"
STATUS_CHALLENGED = "CHALLENGED"
STATUS_SETTLED = "SETTLED"
STATUS_ZERO_UTIL = "REFUNDABLE_ZERO_UTILIZATION"
STATUS_REFUNDABLE = "PRINCIPAL_REFUND_CLAIMABLE"
STATUS_RELEASED = "PRINCIPAL_RELEASED"
STATUS_DEPLOYED = "DEPLOYED_LIVE"
STATUS_RECONCILED = "RECONCILED_LIVE"
POS_UNDEPLOYED = "UNDEPLOYED"
GRID_MIN_LEG = 10 ** 15
GRID_MAX_LEG = 10 ** 21
GRID_YES, GRID_NO = "YES", "NO"
GRID_OPEN, GRID_FINAL, GRID_REFUNDED = "OPEN", "FINALIZED", "REFUNDED"
PHASE_COLLECT = "COLLECTING_SUBSCRIPTIONS"
PHASE_AWAIT_AUDIT = "AWAITING_CONSENSUS_AUDIT"
PHASE_CHALLENGE = "CHALLENGE_WINDOW_OPEN"
PHASE_READY = "READY_TO_SETTLE"
PHASE_REFUNDABLE = "PRINCIPAL_REFUND_OPEN"
PHASE_DONE = "RELEASED"
MAX_BPS = 10000
TAIL_HIGH_CEILING_BPS = 2000
TAIL_BALANCED_CEILING_BPS = 5000
TAIL_DEFENSIVE_CEILING_BPS = 7500
BAND_SHARE_BPS = {BAND_HIGH: 8000, BAND_BALANCED: 5000, BAND_DEFENSIVE: 2000, BAND_FREEZE: 0}
BAND_FLOOR_BPS = {BAND_HIGH: 250, BAND_BALANCED: 500, BAND_DEFENSIVE: 1000, BAND_FREEZE: 0}
BAND_BONUS_CAP_BPS = {BAND_HIGH: 250, BAND_BALANCED: 250, BAND_DEFENSIVE: 500, BAND_FREEZE: 0}
BAND_JUNIOR_BPS = {BAND_HIGH: 2000, BAND_BALANCED: 1250, BAND_DEFENSIVE: 250, BAND_FREEZE: 0}
MIN_SUBSCRIPTION_WINDOW_SECONDS = 60
MIN_CHALLENGE_WINDOW_SECONDS = 60
MIN_TIMEOUT_WINDOW_SECONDS = 120
MAX_WINDOW_SECONDS = 2592000
MIN_DEPTH_REFERENCE_UNITS = 10 ** 3
MAX_DEPTH_REFERENCE_UNITS = 10 ** 15
MIN_SUBSCRIBE_WEI = 10 ** 15
MAX_SUBSCRIBE_WEI = 10 ** 21
MIN_BOND_WEI = 10 ** 15
MAX_BOND_WEI = 10 ** 20
MIN_EPOCH_CAP_WEI = 10 ** 18
MAX_EPOCH_CAP_WEI = 10 ** 23
MAX_EPOCHS = 64
MAX_CHALLENGES_PER_EPOCH = 2
VOL_KEYS = ("regularMarketPrice", "iv30", "volatility", "close", "price", "last")
DEPTH_KEYS = ("total_pot", "open_interest", "liquidity", "depth")
YES_KEYS = ("yes_pool", "yes_shares", "bid_depth")
NO_KEYS = ("no_pool", "no_shares", "ask_depth")
EPOCH_FIELDS = ("epoch_id", "status", "subscription_close_epoch", "rebalance_challenge_deadline_epoch",
                "rebalance_timeout_epoch", "routing_band", "quantized_volatility_baseline_bps", "quantized_tail_risk_bps",
                "derived_active_deployment_amount", "derived_senior_reserve_buffer", "derived_senior_yield_bps",
                "derived_junior_yield_bps", "senior_commit_wei", "junior_commit_wei", "challenge_count")
CONSENSUS_FIELDS = (
    "routing_band", "quantized_volatility_baseline_bps", "quantized_tail_risk_bps",
    "derived_active_deployment_amount", "derived_senior_reserve_buffer",
    "derived_senior_yield_bps", "derived_junior_yield_bps",
)
ROUTING_PRINCIPLE = (
    "The two answers must agree exactly on all seven fields, no tolerance. routing_band must be identical "
    "and one of HIGH_CONVICTION_DEPLOYMENT, BALANCED_HEDGE_ALLOCATION, DEFENSIVE_COLLATERAL_BUFFER, "
    "CORRELATION_FREEZE_WITHDRAWAL. The two quantized prints must be identical integers on the 250 bps grid "
    "inside 0 to 10000. The four derived integers must be identical. A missing field, an off grid print, or a "
    "band that disagrees with its own tail risk print is not equivalent."
)
REASON_BOND = "bond below the required amount"
REASON_BOUNDS = "bound outside its floor or ceiling"
REASON_BUSY = "an epoch is already in flight"
REASON_CAP = "tranche cap or call size exceeded"
REASON_MISSING = "epoch does not exist"
REASON_CLOSED = "no subscription window is open"
REJECT_PREFIX = "[SOFT-FAIL] "

def _fail(message: str) -> None:
    err = gl.vm.UserError(message)
    err.message = message
    raise err

def _u(value) -> int:
    return 0 if value is None else int(value)

def _clamp(value: int, low: int, high: int) -> int:
    return low if value < low else (high if value > high else value)

def _jnum(value) -> int:
    if isinstance(value, bool) or value is None:
        return -1
    if isinstance(value, int):
        return value if value >= 0 else -1
    if not isinstance(value, (float, str)):
        return -1
    text = str(value).strip().replace(",", "").replace("_", "").replace("%", "")
    head = text.split(".")[0]
    return int(head) if head.isdigit() else -1

def _snap_grid(value) -> int:
    number = _jnum(value)
    if number < 0 or number > MAX_BPS:
        return -1
    return min(((number + 125) // 250) * 250, MAX_BPS)

def _days_from_civil(year: int, month: int, day: int) -> int:
    shifted = year - (1 if month <= 2 else 0)
    era = (shifted if shifted >= 0 else shifted - 399) // 400
    yo = shifted - era * 400
    ms = month + (-3 if month > 2 else 9)
    doy = (153 * ms + 2) // 5 + day - 1
    return era * 146097 + yo * 365 + yo // 4 - yo // 100 + doy - 719468

def _parse_epoch(raw) -> int:
    text = str(raw or "").strip()
    if len(text) < 19 or text[4] != "-" or text[7] != "-" or text[10] not in "Tt " or text[13] != ":" or text[16] != ":":
        return -1
    groups = (text[0:4], text[5:7], text[8:10], text[11:13], text[14:16], text[17:19])
    if not all(part.isdigit() for part in groups):
        return -1
    year, month, day, hour, minute, second = [int(part) for part in groups]
    if month < 1 or month > 12 or day < 1 or day > 31 or hour > 23 or minute > 59 or second > 60:
        return -1
    epoch = _days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second
    tail = text[19:]
    if tail and tail[0] in "+-":
        if len(tail) < 3 or not tail[1:3].isdigit():
            return -1
        offset = int(tail[1:3]) * 3600 + (int(tail[3:5]) * 60 if len(tail) >= 5 and tail[3:5].isdigit() else 0)
        epoch += offset if tail[0] == "-" else -offset
    return epoch

def _addr(value) -> Address:
    text = str(value).strip()
    if len(text) != 42 or not text.startswith("0x"):
        _fail(ERROR_EXPECTED + " address must be 42 character hex")
    try:
        return Address(bytes.fromhex(text[2:]))
    except Exception:
        _fail(ERROR_EXPECTED + " address contains a non hex character")

def _addr_hex(value) -> str:
    return "0x" + bytes(_addr(value).as_bytes).hex()

def _clean_text(raw, limit: int = 9000) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    kept = "".join(" " if ord(char) < 32 or ord(char) == 127 or char in "<>" else char for char in raw.replace("</", "< /"))
    return " ".join(kept.split())[:limit]

def _clean_url(raw, label: str) -> str:
    text = _clean_text(raw, 400)
    body = text[8:] if text.startswith("https://") else ""
    host = body.split("/")[0].split("?")[0]
    if len(body) < 5 or "@" in body or "#" in body or " " in body or "\\" in body:
        _fail(ERROR_EXPECTED + " " + label + " must be a plain https feed URL")
    if "." not in host or host.startswith(".") or host.endswith(".") or "//" in body:
        _fail(ERROR_EXPECTED + " " + label + " host is not qualified")
    return text

def _scan_number(text: str, names: tuple, total: bool) -> int:
    found = 0
    hit = False
    for name in names:
        needle = chr(34) + name + chr(34) + ":"
        cursor = 0
        while True:
            at = text.find(needle, cursor)
            if at < 0:
                break
            cursor = at + len(needle)
            token = text[cursor:].lstrip().split(",")[0].split("}")[0].split("]")[0].strip()
            number = _jnum(token)
            if number < 0:
                continue
            if not total:
                return number
            found += number
            hit = True
    return found if hit else -1

def _render_text(url: str) -> str:
    try:
        raw = gl.nondet.web.render(url, mode="text")
    except gl.vm.UserError:
        raise
    except Exception as exc:
        _fail(ERROR_TRANSIENT + " feed render failed: " + str(exc)[:120])
    text = _clean_text(raw)
    if len(text) < 40:
        _fail(ERROR_EXTERNAL + " feed returned too little readable text")
    return text

def _volatility_bps(macro_text: str) -> int:
    value = _scan_number(macro_text, VOL_KEYS, False)
    if value < 0:
        _fail(ERROR_EXTERNAL + " macro feed has no usable volatility print")
    snapped = _snap_grid(value * 100 if value <= 100 else value)
    if snapped < 0:
        _fail(ERROR_EXTERNAL + " macro volatility print is out of range")
    return snapped

def _depth_tail(depth_text: str, depth_reference: int) -> int:
    yes_side = max(0, _scan_number(depth_text, YES_KEYS, True))
    no_side = max(0, _scan_number(depth_text, NO_KEYS, True))
    open_interest = max(0, _scan_number(depth_text, DEPTH_KEYS, True)) or yes_side + no_side
    if open_interest <= 0:
        _fail(ERROR_EXTERNAL + " ProphetGrid depth has no measurable open interest")
    both = yes_side + no_side
    imbalance = abs(yes_side - no_side) * MAX_BPS // both if both > 0 else 0
    thinness = MAX_BPS - _clamp(open_interest * MAX_BPS // max(1, depth_reference), 0, MAX_BPS)
    tail = _snap_grid((imbalance + thinness) // 2)
    if tail < 0:
        _fail(ERROR_EXTERNAL + " tail risk print is off the 250 bps grid")
    return tail

def _derive_routing_fields(volatility_bps: int, tail_risk_bps: int, senior_commit: int, junior_commit: int) -> dict:
    band = (BAND_HIGH if tail_risk_bps <= TAIL_HIGH_CEILING_BPS else BAND_BALANCED if tail_risk_bps <= TAIL_BALANCED_CEILING_BPS
            else BAND_DEFENSIVE if tail_risk_bps <= TAIL_DEFENSIVE_CEILING_BPS else BAND_FREEZE)
    liquidity = senior_commit + junior_commit
    deployment = liquidity * BAND_SHARE_BPS[band] // MAX_BPS
    spread = max(0, volatility_bps // 8 - tail_risk_bps // 4)
    return {
        "routing_band": band,
        "quantized_volatility_baseline_bps": volatility_bps,
        "quantized_tail_risk_bps": tail_risk_bps,
        "derived_active_deployment_amount": deployment,
        "derived_senior_reserve_buffer": liquidity - deployment,
        "derived_senior_yield_bps": min(BAND_FLOOR_BPS[band] + min(BAND_BONUS_CAP_BPS[band], volatility_bps // 4), 7500),
        "derived_junior_yield_bps": min(BAND_JUNIOR_BPS[band] + spread, 7500),
    }

@allow_storage
@dataclass
class EpochRecord:
    epoch_id: u256
    status: str
    proposed_by: Address
    subscription_close_epoch: u256
    rebalance_challenge_deadline_epoch: u256
    rebalance_timeout_epoch: u256
    depth_reference_units: u256
    senior_commit_wei: u256
    junior_commit_wei: u256
    routing_band: str
    quantized_volatility_baseline_bps: u256
    quantized_tail_risk_bps: u256
    derived_active_deployment_amount: u256
    derived_senior_reserve_buffer: u256
    derived_senior_yield_bps: u256
    derived_junior_yield_bps: u256
    challenge_count: u256

@allow_storage
@dataclass
class MarketPosition:
    target_market_id: str
    deployed_capital_wei: u256
    yes_shares_recorded: u256
    no_shares_recorded: u256
    prophet_grid_entry_snapshot: str
    deployment_status: str
    reconciled: bool
    reconciled_return_wei: u256
    realized_gain_wei: u256
    realized_loss_wei: u256

@gl.evm.contract_interface
class NativeCustodyAccount:
    class View:
        pass

    class Write:
        pass

class ProphetSyndicate(gl.Contract):
    linked_prophet_grid_address: str
    linked_probe_status: str
    macro_feed_url: str
    market_depth_url: str
    pst_total_supply: u256
    vault_custody_balance: u256
    native_custody_balance: u256
    senior_tranche_principal: TreeMap[Address, u256]
    junior_tranche_principal: TreeMap[Address, u256]
    senior_principal_total: u256
    junior_principal_total: u256
    lp_index: DynArray[str]
    refundable_balances: TreeMap[Address, u256]
    refundable_total: u256
    bond_custody: TreeMap[Address, u256]
    bond_total: u256
    syndicate_yield_pool_wei: u256
    last_payable_rejection: TreeMap[Address, str]
    epochs: TreeMap[str, EpochRecord]
    epoch_ids: DynArray[str]
    next_epoch_id: u256
    tranche_epoch_cap_wei: u256
    routing_bond_wei: u256
    default_subscription_window_seconds: u256
    default_challenge_window_seconds: u256
    default_timeout_window_seconds: u256
    positions: TreeMap[str, MarketPosition]
    active_deployed_to_prophet_grid: u256
    reconciled_proceeds_total: u256

    def __init__(self, linked_prophet_grid_address: str, macro_feed_url: str, market_depth_url: str,
                 tranche_epoch_cap_wei: u256, routing_bond_wei: u256, subscription_window_seconds: u256,
                 challenge_window_seconds: u256, timeout_window_seconds: u256) -> None:
        self.linked_prophet_grid_address = _addr_hex(linked_prophet_grid_address or DEFAULT_LINKED_PROPHET_GRID_ADDRESS)
        self.linked_probe_status = "NOT_PROBED"
        self.macro_feed_url = _clean_url(macro_feed_url or DEFAULT_MACRO_FEED_URL, "macro feed")
        self.market_depth_url = _clean_url(market_depth_url or DEFAULT_MARKET_DEPTH_URL, "depth feed")
        self.next_epoch_id = u256(1)
        self.tranche_epoch_cap_wei = u256(self._bound(tranche_epoch_cap_wei, MIN_EPOCH_CAP_WEI, MAX_EPOCH_CAP_WEI))
        self.routing_bond_wei = u256(self._bound(routing_bond_wei, MIN_BOND_WEI, MAX_BOND_WEI))
        self.default_subscription_window_seconds = u256(self._bound(subscription_window_seconds, MIN_SUBSCRIPTION_WINDOW_SECONDS, MAX_WINDOW_SECONDS))
        self.default_challenge_window_seconds = u256(self._bound(challenge_window_seconds, MIN_CHALLENGE_WINDOW_SECONDS, MAX_WINDOW_SECONDS))
        self.default_timeout_window_seconds = u256(self._bound(timeout_window_seconds, MIN_TIMEOUT_WINDOW_SECONDS, MAX_WINDOW_SECONDS))
        self.active_deployed_to_prophet_grid = u256(0)
        self.reconciled_proceeds_total = u256(0)

    def _bound(self, value, low: int, high: int) -> int:
        number = _jnum(value)
        if number < low or number > high:
            _fail(ERROR_EXPECTED + REASON_BOUNDS)
        return number

    def _now(self) -> int:
        epoch = _parse_epoch(str(gl.message_raw["datetime"]))
        if epoch < 0:
            _fail(ERROR_EXPECTED + " transaction datetime is unparsable")
        return epoch

    def _bucket(self, table, key) -> int:
        return _u(table.get(key, u256(0)))

    def _require_epoch(self, epoch_id) -> EpochRecord:
        record = self.epochs.get(str(int(_u(epoch_id))))
        if record is None:
            _fail(ERROR_EXPECTED + " " + REASON_MISSING)
        return record

    def _save(self, record: EpochRecord) -> None:
        self.epochs[str(int(_u(record.epoch_id)))] = record

    def _in_flight(self) -> EpochRecord:
        for key in self.epoch_ids:
            record = self.epochs.get(key)
            if record is not None and record.status not in (STATUS_SETTLED, STATUS_RELEASED):
                return record
        return None

    def _phase(self, record: EpochRecord) -> str:
        now = self._now()
        if record.status in (STATUS_SETTLED, STATUS_RELEASED):
            return PHASE_DONE
        if record.status in (STATUS_ZERO_UTIL, STATUS_REFUNDABLE):
            return PHASE_REFUNDABLE
        if record.status == STATUS_FINALIZED:
            if now >= _u(record.rebalance_timeout_epoch):
                return PHASE_REFUNDABLE
            return PHASE_CHALLENGE if now < _u(record.rebalance_challenge_deadline_epoch) else PHASE_READY
        if now >= _u(record.rebalance_timeout_epoch):
            return PHASE_REFUNDABLE
        if record.status == STATUS_OPEN and now < _u(record.subscription_close_epoch):
            return PHASE_COLLECT
        return PHASE_AWAIT_AUDIT

    def _table(self, tranche: str):
        return self.senior_tranche_principal if tranche == TRANCHE_SENIOR else self.junior_tranche_principal

    def _owe(self, account: Address, amount: int) -> None:
        if amount > 0:
            self.refundable_balances[account] = u256(self._bucket(self.refundable_balances, account) + amount)
            self.refundable_total = u256(_u(self.refundable_total) + amount)
        return None

    def _soft_fail(self, sender: Address, value: int, reason: str) -> str:
        self.native_custody_balance = u256(_u(self.native_custody_balance) + value)
        self._owe(sender, value)
        self.last_payable_rejection[sender] = REJECT_PREFIX + reason
        return json.dumps({"accepted": False, "reason": reason, "refundable_wei": str(self._bucket(self.refundable_balances, sender))})

    def _mint_principal(self, account: Address, tranche: str, amount: int) -> None:
        table = self._table(tranche)
        table[account] = u256(self._bucket(table, account) + amount)
        if tranche == TRANCHE_SENIOR:
            self.senior_principal_total = u256(_u(self.senior_principal_total) + amount)
        else:
            self.junior_principal_total = u256(_u(self.junior_principal_total) + amount)
        self.vault_custody_balance = u256(_u(self.vault_custody_balance) + amount)
        self.pst_total_supply = u256(_u(self.pst_total_supply) + amount)

    def _release_principal(self, account: Address, tranche: str) -> int:
        table = self._table(tranche)
        amount = self._bucket(table, account)
        if amount <= 0:
            return 0
        table[account] = u256(0)
        if tranche == TRANCHE_SENIOR:
            self.senior_principal_total = u256(_u(self.senior_principal_total) - amount)
        else:
            self.junior_principal_total = u256(_u(self.junior_principal_total) - amount)
        self.vault_custody_balance = u256(_u(self.vault_custody_balance) - amount)
        self.pst_total_supply = u256(_u(self.pst_total_supply) - amount)
        return amount

    def _release_bond(self, account: Address, amount: int, forfeit: bool) -> None:
        moving = min(amount, self._bucket(self.bond_custody, account))
        if moving <= 0:
            return
        self.bond_custody[account] = u256(self._bucket(self.bond_custody, account) - moving)
        self.bond_total = u256(_u(self.bond_total) - moving)
        if forfeit:
            self.syndicate_yield_pool_wei = u256(_u(self.syndicate_yield_pool_wei) + moving)
        else:
            self._owe(account, moving)

    def _pay_native(self, recipient: Address, amount: int) -> bool:
        if amount <= 0:
            return False
        try:
            gl.get_contract_at(recipient).emit_transfer(value=u256(amount))
            return True
        except Exception:
            return False

    def _account_native_balance(self) -> int:
        try:
            return _u(NativeCustodyAccount(gl.message.contract_address).balance)
        except Exception:
            return -1

    def _self_hex(self) -> str:
        return "0x" + bytes(gl.message.contract_address.as_bytes).hex()

    def _grid(self):
        return gl.get_contract_at(_addr(self.linked_prophet_grid_address))

    def _shave_principal(self, loss: int) -> None:
        for tranche in (TRANCHE_JUNIOR, TRANCHE_SENIOR):
            table = self._table(tranche)
            for holder in self.lp_index:
                if loss <= 0:
                    break
                account = _addr(holder)
                cut = min(self._bucket(table, account), loss)
                if cut <= 0:
                    continue
                table[account] = u256(self._bucket(table, account) - cut)
                total = self.senior_principal_total if tranche == TRANCHE_SENIOR else self.junior_principal_total
                if tranche == TRANCHE_SENIOR:
                    self.senior_principal_total = u256(_u(total) - cut)
                else:
                    self.junior_principal_total = u256(_u(total) - cut)
                self.vault_custody_balance = u256(_u(self.vault_custody_balance) - cut)
                self.pst_total_supply = u256(_u(self.pst_total_supply) - cut)
                loss -= cut

    def _compute_syndicate_epoch_state(self, record: EpochRecord) -> dict:
        floor_bps = BAND_FLOOR_BPS.get(record.routing_band, 0)
        state = {}
        for name in EPOCH_FIELDS:
            value = getattr(record, name)
            state[name] = _addr_hex(value) if isinstance(value, Address) else str(_u(value) if isinstance(value, int) else value)
        state["phase"] = self._phase(record)
        state["senior_floor_wei"] = str(_u(record.senior_commit_wei) * floor_bps // MAX_BPS)
        position = self.positions.get(state["epoch_id"])
        state["prophet_grid_deployment_status"] = position.deployment_status if position is not None else POS_UNDEPLOYED
        state["prophet_grid_reconciled"] = bool(position is not None and position.reconciled)
        return state

    @gl.public.write.payable
    def propose_routing_epoch_payable(self, depth_reference_units: u256) -> str:
        sender = gl.message.sender_address
        value = _u(gl.message.value)
        bond = _u(self.routing_bond_wei)
        if value < bond:
            return self._soft_fail(sender, value, REASON_BOND)
        depth = _jnum(depth_reference_units)
        if depth < MIN_DEPTH_REFERENCE_UNITS or depth > MAX_DEPTH_REFERENCE_UNITS:
            return self._soft_fail(sender, value, REASON_BOUNDS)
        if len(self.epoch_ids) >= MAX_EPOCHS or self._in_flight() is not None:
            return self._soft_fail(sender, value, REASON_BUSY)
        now = self._now()
        sub = _u(self.default_subscription_window_seconds)
        chal = _u(self.default_challenge_window_seconds)
        audit = _u(self.default_timeout_window_seconds)
        epoch_id = _u(self.next_epoch_id)
        record = EpochRecord(
            u256(epoch_id), STATUS_OPEN, sender, u256(now + sub), u256(now + sub + chal), u256(now + sub + audit),
            u256(depth), u256(0), u256(0), "", u256(0), u256(0), u256(0), u256(0), u256(0), u256(0), u256(0),
        )
        self.native_custody_balance = u256(_u(self.native_custody_balance) + value)
        self.bond_custody[sender] = u256(self._bucket(self.bond_custody, sender) + bond)
        self.bond_total = u256(_u(self.bond_total) + bond)
        self._owe(sender, value - bond)
        self.epochs[str(epoch_id)] = record
        self.epoch_ids.append(str(epoch_id))
        self.next_epoch_id = u256(epoch_id + 1)
        self.last_payable_rejection[sender] = ""
        return str(epoch_id) + ":" + record.status

    def _subscribe(self, tranche: str) -> str:
        sender = gl.message.sender_address
        value = _u(gl.message.value)
        if value <= 0:
            return self._soft_fail(sender, value, "call carried no native value")
        record = self._in_flight()
        if record is None or self._phase(record) != PHASE_COLLECT:
            return self._soft_fail(sender, value, REASON_CLOSED)
        if value < MIN_SUBSCRIBE_WEI or value > MAX_SUBSCRIBE_WEI:
            return self._soft_fail(sender, value, "below the minimum funded size" if value < MIN_SUBSCRIBE_WEI else REASON_CAP)
        locked = _u(self.senior_principal_total) if tranche == TRANCHE_SENIOR else _u(self.junior_principal_total)
        if locked + value > _u(self.tranche_epoch_cap_wei):
            return self._soft_fail(sender, value, REASON_CAP)
        holder = _addr_hex(sender)
        fee = value * ENTRY_FEE_BPS // MAX_BPS
        self.native_custody_balance = u256(_u(self.native_custody_balance) + value)
        self._mint_principal(sender, tranche, value - fee)
        self.syndicate_yield_pool_wei = u256(_u(self.syndicate_yield_pool_wei) + fee)
        if holder not in self.lp_index:
            self.lp_index.append(holder)
        self.last_payable_rejection[sender] = ""
        return tranche + ":" + str(value - fee) + ":" + str(fee)

    @gl.public.write.payable
    def subscribe_senior_tranche_payable(self) -> str:
        return self._subscribe(TRANCHE_SENIOR)

    @gl.public.write.payable
    def subscribe_junior_tranche_payable(self) -> str:
        return self._subscribe(TRANCHE_JUNIOR)

    @gl.public.write.payable
    def challenge_routing_allocation_payable(self, epoch_id: u256) -> str:
        sender = gl.message.sender_address
        value = _u(gl.message.value)
        bond = 2 * _u(self.routing_bond_wei)
        if value < bond:
            return self._soft_fail(sender, value, REASON_BOND)
        record = self._require_epoch(epoch_id)
        if self._phase(record) != PHASE_CHALLENGE or _u(record.challenge_count) >= MAX_CHALLENGES_PER_EPOCH:
            return self._soft_fail(sender, value, "no challenge slot is open on this epoch")
        self.native_custody_balance = u256(_u(self.native_custody_balance) + value)
        self._owe(sender, value - bond)
        self.syndicate_yield_pool_wei = u256(_u(self.syndicate_yield_pool_wei) + bond)
        record.status = STATUS_CHALLENGED
        record.challenge_count = u256(_u(record.challenge_count) + 1)
        self._save(record)
        self.last_payable_rejection[sender] = ""
        return str(_u(record.epoch_id)) + ":" + record.status + ":" + str(bond)

    @gl.public.write
    def execute_consensus_rebalance(self, epoch_id: u256) -> str:
        record = self._require_epoch(epoch_id)
        if self._phase(record) != PHASE_AWAIT_AUDIT:
            _fail(ERROR_EXPECTED + " rebalance runs only after subscription close and before the audit timeout")
        senior_commit = _u(self.senior_principal_total)
        junior_commit = _u(self.junior_principal_total)
        try:
            self.linked_probe_status = "VERIFIED_" + str(_u(self._grid().view().get_market_count())) + " PROPHETGRID MARKETS"
        except Exception:
            self.linked_probe_status = "UNAVAILABLE"
        macro_url = self.macro_feed_url
        depth_url = self.market_depth_url
        depth_reference = _u(record.depth_reference_units)

        def leader_fn():
            volatility = _volatility_bps(_render_text(macro_url))
            tail = _depth_tail(_render_text(depth_url), depth_reference)
            return _derive_routing_fields(volatility, tail, senior_commit, junior_commit)

        consensus = gl.eq_principle.prompt_comparative(leader_fn, ROUTING_PRINCIPLE)
        if not isinstance(consensus, dict):
            _fail(ERROR_LLM + " routing consensus did not return an object")
        model = _derive_routing_fields(
            _jnum(consensus.get("quantized_volatility_baseline_bps")), _jnum(consensus.get("quantized_tail_risk_bps")),
            senior_commit, junior_commit,
        )
        for name in CONSENSUS_FIELDS:
            if str(consensus.get(name)) != str(model[name]):
                _fail(ERROR_EXPECTED + " consensus " + name + " is not the deterministic answer of its own prints")
        if model["derived_active_deployment_amount"] + model["derived_senior_reserve_buffer"] != senior_commit + junior_commit:
            _fail(ERROR_EXPECTED + " routing consensus did not conserve epoch liquidity")
        now = self._now()
        chal = _u(self.default_challenge_window_seconds)
        record = EpochRecord(
            record.epoch_id, STATUS_FINALIZED if model["derived_active_deployment_amount"] > 0 else STATUS_ZERO_UTIL,
            record.proposed_by, record.subscription_close_epoch, u256(now + chal),
            u256(max(_u(record.rebalance_timeout_epoch), now + chal + _u(self.default_timeout_window_seconds))),
            record.depth_reference_units, u256(senior_commit), u256(junior_commit), model["routing_band"],
            u256(model["quantized_volatility_baseline_bps"]), u256(model["quantized_tail_risk_bps"]),
            u256(model["derived_active_deployment_amount"]), u256(model["derived_senior_reserve_buffer"]),
            u256(model["derived_senior_yield_bps"]), u256(model["derived_junior_yield_bps"]),
            record.challenge_count,
        )
        self._save(record)
        return json.dumps(self._compute_syndicate_epoch_state(record))

    @gl.public.write
    def deploy_routed_capital_to_prophet_grid(self, epoch_id: u256, target_market_id: str, yes_leg_amount: u256, no_leg_amount: u256) -> str:
        record = self._require_epoch(epoch_id)
        if record.status != STATUS_FINALIZED:
            _fail(ERROR_EXPECTED + " only a finalized epoch with routed capital may deploy")
        key = str(_u(record.epoch_id))
        if self.positions.get(key) is not None:
            _fail(ERROR_EXPECTED + " this epoch already has a ProphetGrid position")
        routed = _u(record.derived_active_deployment_amount)
        if routed <= 0:
            _fail(ERROR_EXPECTED + " this epoch routed no capital to ProphetGrid")
        market = _clean_text(target_market_id, 16)
        if not market.isdigit() or not 0 < int(market) <= 1000000:
            _fail(ERROR_EXPECTED + " target market id must be a bounded decimal")
        yes_leg = _u(yes_leg_amount)
        no_leg = _u(no_leg_amount)
        if yes_leg + no_leg != routed:
            _fail(ERROR_EXPECTED + " market making legs must equal the routed deployment exactly")
        if not GRID_MIN_LEG <= yes_leg <= GRID_MAX_LEG or not GRID_MIN_LEG <= no_leg <= GRID_MAX_LEG:
            _fail(ERROR_EXPECTED + " each ProphetGrid leg must stay inside the staking bounds")
        if routed > _u(self.native_custody_balance):
            _fail(ERROR_EXPECTED + " the vault does not hold the capital being deployed")
        grid = self._grid()
        grid.emit(value=u256(yes_leg), on="finalized").acquire_outcome_shares_payable(market, GRID_YES)
        grid.emit(value=u256(no_leg), on="finalized").acquire_outcome_shares_payable(market, GRID_NO)
        self.native_custody_balance = u256(_u(self.native_custody_balance) - routed)
        self.active_deployed_to_prophet_grid = u256(_u(self.active_deployed_to_prophet_grid) + routed)
        self.positions[key] = MarketPosition(market, u256(routed), u256(yes_leg), u256(no_leg),
                                             "PROPHETGRID " + market + " DUAL_LEG_DEPLOYED", STATUS_DEPLOYED, False, u256(0), u256(0), u256(0))
        return json.dumps({"epoch_id": key, "target_market_id": market, "deployed_capital_wei": str(routed),
                           "yes_leg_wei": str(yes_leg), "no_leg_wei": str(no_leg), "deployment_status": STATUS_DEPLOYED})

    @gl.public.write
    def reconcile_prophet_grid_position(self, epoch_id: u256) -> str:
        record = self._require_epoch(epoch_id)
        key = str(_u(record.epoch_id))
        position = self.positions.get(key)
        if position is None:
            _fail(ERROR_EXPECTED + " no ProphetGrid position is recorded for this epoch")
        if position.deployment_status != STATUS_DEPLOYED:
            _fail(ERROR_EXPECTED + " this ProphetGrid position is not awaiting reconciliation")
        market = position.target_market_id
        raw = self._grid().view().preview_payout(market, self._self_hex())
        book = json.loads(_clean_text(raw, 6000))
        deployed = _u(position.deployed_capital_wei)
        status = str(book.get("status", ""))
        estimated = _u(book.get("estimated_payout", 0))
        refundable = _u(book.get("refund_amount", 0))
        principal = _u(book.get("principal", 0))
        grid = self._grid()
        if status == GRID_FINAL and estimated > 0:
            proceeds = estimated
            grid.emit(on="finalized").claim_market_winnings(market)
        else:
            proceeds = refundable if refundable > 0 else (principal if principal > 0 else deployed)
            grid.emit(on="finalized").claim_refund(market)
        gain = proceeds - deployed if proceeds > deployed else 0
        loss = deployed - proceeds if proceeds < deployed else 0
        self.active_deployed_to_prophet_grid = u256(_u(self.active_deployed_to_prophet_grid) - deployed)
        self.reconciled_proceeds_total = u256(_u(self.reconciled_proceeds_total) + proceeds)
        if gain > 0:
            self.syndicate_yield_pool_wei = u256(_u(self.syndicate_yield_pool_wei) + gain)
        if loss > 0:
            self._shave_principal(loss)
            cut_j = min(loss, _u(record.junior_commit_wei))
            record.junior_commit_wei = u256(_u(record.junior_commit_wei) - cut_j)
            record.senior_commit_wei = u256(_u(record.senior_commit_wei) - (loss - cut_j))
            self._save(record)
        position.yes_shares_recorded = u256(_u(book.get("yes_shares", 0)) or _u(position.yes_shares_recorded))
        position.no_shares_recorded = u256(_u(book.get("no_shares", 0)) or _u(position.no_shares_recorded))
        position.prophet_grid_entry_snapshot = _clean_text(raw, 900)
        position.deployment_status = STATUS_RECONCILED
        position.reconciled = True
        position.reconciled_return_wei = u256(proceeds)
        position.realized_gain_wei = u256(gain)
        position.realized_loss_wei = u256(loss)
        self.positions[key] = position
        return json.dumps({"epoch_id": key, "target_market_id": market, "reconciled_return_wei": str(proceeds),
                           "realized_gain_wei": str(gain), "realized_loss_wei": str(loss), "reconciled": True})

    @gl.public.write
    def settle_tranche_epoch(self, epoch_id: u256) -> str:
        record = self._require_epoch(epoch_id)
        if self._phase(record) != PHASE_READY:
            _fail(ERROR_EXPECTED + " settlement needs a finalized epoch past its challenge deadline")
        routed = _u(record.derived_active_deployment_amount)
        position = self.positions.get(str(_u(record.epoch_id)))
        if routed > 0 and (position is None or not position.reconciled):
            _fail(ERROR_EXPECTED + " routed capital must be deployed to and reconciled with ProphetGrid before settlement")
        pool = _u(self.syndicate_yield_pool_wei)
        paid_total = 0
        payouts = {}
        for tranche in (TRANCHE_SENIOR, TRANCHE_JUNIOR):
            rate = _u(record.derived_senior_yield_bps) if tranche == TRANCHE_SENIOR else _u(record.derived_junior_yield_bps)
            table = self._table(tranche)
            paid = 0
            released = 0
            for holder in self.lp_index:
                account = _addr(holder)
                balance = self._bucket(table, account)
                if balance > 0:
                    share = min(balance * rate // MAX_BPS, pool - paid_total - paid)
                    released += self._release_principal(account, tranche)
                    self._owe(account, balance + share)
                    paid += share
            paid_total += paid
            payouts[tranche] = (paid, released)
        self.syndicate_yield_pool_wei = u256(pool - paid_total)
        self._release_bond(record.proposed_by, _u(self.routing_bond_wei), False)
        record.status = STATUS_SETTLED
        self._save(record)
        return json.dumps({
            "epoch_id": str(_u(record.epoch_id)), "routing_band": record.routing_band, "settled": True,
            "senior_yield_wei": str(payouts[TRANCHE_SENIOR][0]), "senior_principal_wei": str(payouts[TRANCHE_SENIOR][1]),
            "junior_yield_wei": str(payouts[TRANCHE_JUNIOR][0]), "junior_principal_wei": str(payouts[TRANCHE_JUNIOR][1]),
            "pool_used_wei": str(paid_total), "pool_left_wei": str(_u(self.syndicate_yield_pool_wei)),
            "reconciled_return_wei": str(_u(position.reconciled_return_wei)) if position is not None else "0",
            "realized_gain_wei": str(_u(position.realized_gain_wei)) if position is not None else "0",
            "realized_loss_wei": str(_u(position.realized_loss_wei)) if position is not None else "0",
        })

    @gl.public.write
    def claim_epoch_principal_refund(self, epoch_id: u256) -> str:
        record = self._require_epoch(epoch_id)
        if self._phase(record) != PHASE_REFUNDABLE:
            _fail(ERROR_EXPECTED + " this epoch has no open principal refund")
        if record.status in (STATUS_OPEN, STATUS_CHALLENGED):
            record.status = STATUS_REFUNDABLE
        account = gl.message.sender_address
        moved = self._release_principal(account, TRANCHE_SENIOR)
        moved += self._release_principal(account, TRANCHE_JUNIOR)
        if moved <= 0:
            _fail(ERROR_EXPECTED + " no locked principal left")
        self._owe(account, moved)
        if _u(self.senior_principal_total) + _u(self.junior_principal_total) <= 0:
            self._release_bond(record.proposed_by, _u(self.routing_bond_wei), record.status == STATUS_REFUNDABLE)
            record.status = STATUS_RELEASED
        self._save(record)
        return json.dumps({"epoch_id": str(_u(record.epoch_id)), "refunded_principal_wei": str(moved), "status": record.status})

    @gl.public.write
    def withdraw_refund(self, amount: u256) -> str:
        account = gl.message.sender_address
        owed = self._bucket(self.refundable_balances, account)
        wanted = _u(amount)
        moving = owed if wanted <= 0 or wanted > owed else wanted
        if moving <= 0:
            self.last_payable_rejection[account] = REJECT_PREFIX + "no claimable credit to move"
            return json.dumps({"paid_wei": "0", "credit_wei": str(owed)})
        self.refundable_balances[account] = u256(owed - moving)
        self.refundable_total = u256(_u(self.refundable_total) - moving)
        if self._pay_native(account, moving):
            self.native_custody_balance = u256(_u(self.native_custody_balance) - moving)
            self.last_payable_rejection[account] = ""
            return json.dumps({"paid_wei": str(moving), "credit_wei": str(owed - moving)})
        self._owe(account, moving)
        self.last_payable_rejection[account] = REJECT_PREFIX + "native payout unavailable, credit preserved"
        return json.dumps({"paid_wei": "0", "preserved_wei": str(moving)})

    @gl.public.write
    def claim_refundable_balance(self) -> u256:
        account = gl.message.sender_address
        before = self._bucket(self.refundable_balances, account)
        self.withdraw_refund(u256(before))
        return u256(before - self._bucket(self.refundable_balances, account))

    @gl.public.view
    def get_refundable_balance(self, account: str) -> u256:
        return u256(self._bucket(self.refundable_balances, _addr(account)))

    @gl.public.view
    def get_tranche_position(self, account: str) -> str:
        holder = _addr(account)
        senior = self._bucket(self.senior_tranche_principal, holder)
        junior = self._bucket(self.junior_tranche_principal, holder)
        record = self._in_flight()
        return json.dumps({
            "account": _addr_hex(holder), "senior_principal_wei": str(senior), "junior_principal_wei": str(junior),
            "pst_balance_wei": str(senior + junior), "claimable_wei": str(self._bucket(self.refundable_balances, holder)),
            "locked_bond_wei": str(self._bucket(self.bond_custody, holder)), "last_rejection": self.last_payable_rejection.get(holder, ""),
            "active_epoch": self._compute_syndicate_epoch_state(record) if record is not None else {},
        })

    @gl.public.view
    def get_epoch_prophet_grid_position(self, epoch_id: u256) -> dict:
        key = str(int(_u(epoch_id)))
        position = self.positions.get(key)
        shared = {"epoch_id": key, "active_deployed_to_prophet_grid": str(_u(self.active_deployed_to_prophet_grid)),
                  "reconciled_proceeds_total": str(_u(self.reconciled_proceeds_total))}
        if position is None:
            shared["has_position"] = False
            shared["deployment_status"] = POS_UNDEPLOYED
            return shared
        shared.update({
            "has_position": True, "target_market_id": position.target_market_id,
            "deployed_capital_wei": str(_u(position.deployed_capital_wei)),
            "yes_shares_recorded": str(_u(position.yes_shares_recorded)), "no_shares_recorded": str(_u(position.no_shares_recorded)),
            "prophet_grid_entry_snapshot": position.prophet_grid_entry_snapshot,
            "deployment_status": position.deployment_status, "reconciled": bool(position.reconciled),
            "reconciled_return_wei": str(_u(position.reconciled_return_wei)),
            "realized_gain_wei": str(_u(position.realized_gain_wei)), "realized_loss_wei": str(_u(position.realized_loss_wei)),
        })
        return shared

    @gl.public.view
    def get_custody_accounting_summary(self) -> str:
        senior = _u(self.senior_principal_total)
        junior = _u(self.junior_principal_total)
        claimable = _u(self.refundable_total)
        bonds = _u(self.bond_total)
        pool = _u(self.syndicate_yield_pool_wei)
        bucketed = senior + junior + claimable + bonds + pool
        deployed = _u(self.active_deployed_to_prophet_grid)
        proceeds = _u(self.reconciled_proceeds_total)
        total_assets = _u(self.native_custody_balance) + deployed + proceeds
        reserved = 0
        unfunded = 0
        rows = []
        for key in self.epoch_ids:
            record = self.epochs.get(key)
            if record is None:
                continue
            state = self._compute_syndicate_epoch_state(record)
            rows.append(state)
            if state["phase"] != PHASE_DONE:
                reserved += int(state["senior_commit_wei"]) + int(state["junior_commit_wei"])
                unfunded += max(0, int(state["senior_floor_wei"]) - pool)
        checks = {
            "custody_partition_exact": bucketed == total_assets,
            "pst_backed_by_custody": _u(self.pst_total_supply) == _u(self.vault_custody_balance) == senior + junior,
            "locked_principal_reserved": reserved <= total_assets,
            "deployed_within_custody": deployed <= total_assets,
        }
        return json.dumps({
            "ok": all(checks.values()), "checks": checks, "clock_epoch": str(self._now()), "epochs": rows,
            "partition": {"senior_principal_wei": str(senior), "junior_principal_wei": str(junior),
                          "claimable_wei": str(claimable), "locked_bond_wei": str(bonds), "yield_pool_wei": str(pool)},
            "native_custody_balance": str(_u(self.native_custody_balance)),
            "active_deployed_to_prophet_grid": str(deployed), "reconciled_proceeds_total": str(proceeds),
            "total_custody_assets_wei": str(total_assets),
            "vault_custody_balance": str(_u(self.vault_custody_balance)), "pst_total_supply": str(_u(self.pst_total_supply)),
            "epoch_count": str(len(self.epoch_ids)), "vm_account_balance_wei": str(self._account_native_balance()),
            "reserved_liability_wei": str(reserved), "unfunded_floor_wei": str(unfunded),
            "linked_prophet_grid_address": self.linked_prophet_grid_address, "linked_probe_status": self.linked_probe_status,
            "macro_feed_url": self.macro_feed_url, "market_depth_url": self.market_depth_url,
            "runner_pin": RUNNER_PIN, "principle": ROUTING_PRINCIPLE,
            "limits": {"tranche_epoch_cap_wei": str(_u(self.tranche_epoch_cap_wei)),
                       "routing_bond_wei": str(_u(self.routing_bond_wei)), "challenge_bond_wei": str(_u(2 * self.routing_bond_wei)),
                       "entry_fee_bps": str(ENTRY_FEE_BPS), "min_subscription_wei": str(MIN_SUBSCRIBE_WEI),
                       "subscription_seconds": str(_u(self.default_subscription_window_seconds)),
                       "challenge_seconds": str(_u(self.default_challenge_window_seconds)),
                       "audit_timeout_seconds": str(_u(self.default_timeout_window_seconds))},
        })
