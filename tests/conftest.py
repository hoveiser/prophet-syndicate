"""Shared fixtures and mock helpers for ProphetSyndicate direct mode tests."""

import json
import os
import sys
import tempfile

import pytest

CONTRACT = "contracts/prophet_syndicate.py"


def _patch_gltest_fd0_for_windows():
    """The gltest direct loader unlinks its fd0 temp file while it is still open,
    which raises PermissionError on Windows. Reinstall the injector without the
    unlink so the same message context setup works on this platform. Confined to
    the test harness, never the contract."""
    try:
        from gltest.direct import loader as gl_loader
    except Exception:
        return
    if getattr(gl_loader, "_fd0_windows_patched", False):
        return

    def _inject_message_to_fd0(vm):
        from genlayer.py import calldata
        from genlayer.py.types import Address

        sender_addr = vm.sender
        if isinstance(sender_addr, bytes):
            sender_addr = Address(sender_addr)
        contract_addr = vm._contract_address
        if isinstance(contract_addr, bytes):
            contract_addr = Address(contract_addr)
        origin_addr = vm.origin
        if isinstance(origin_addr, bytes):
            origin_addr = Address(origin_addr)
        message_data = {
            "contract_address": contract_addr,
            "sender_address": sender_addr,
            "origin_address": origin_addr,
            "stack": [],
            "value": vm._value,
            "datetime": vm._datetime,
            "is_init": False,
            "chain_id": vm._chain_id,
            "entry_kind": 0,
            "entry_data": b"",
            "entry_stage_data": None,
        }
        encoded = calldata.encode(message_data)
        fd, path = tempfile.mkstemp()
        try:
            os.write(fd, encoded)
            os.lseek(fd, 0, os.SEEK_SET)
            vm._original_stdin_fd = os.dup(0)
            os.dup2(fd, 0)
        finally:
            os.close(fd)
            # Deferred cleanup only when it is safe; never crash on Windows.
            try:
                os.unlink(path)
            except OSError:
                pass

    gl_loader._inject_message_to_fd0 = _inject_message_to_fd0
    gl_loader._fd0_windows_patched = True


_patch_gltest_fd0_for_windows()

# 1 GEN is 10 ** 18 wei. Subscriptions below MIN_SUBSCRIBE_WEI are rejected.
GEN = 10 ** 18

# ProphetGrid books are denominated in collateral units, not wei. A reference book
# deeper than the live book pushes the tail risk print up, a thinner one pushes it down.
DEEP = 10 ** 6
SHALLOW = 10 ** 9

MACRO_JSON = json.dumps({"chart": {"result": [{"meta": {"symbol": "^VIX", "regularMarketPrice": 18.53}}]}})

# Two ProphetGrid markets with 1.6M of open interest and a 3:1 YES to NO skew.
DEPTH_JSON = json.dumps(
    {
        "markets": [
            {"total_pot": 1000000, "yes_pool": 750000, "no_pool": 250000},
            {"total_pot": 600000, "yes_pool": 450000, "no_pool": 150000},
        ],
        "platform_stats": {"total_volume": 2800000},
    }
)


def deploy(direct_deploy):
    """Deploy with windows short enough for a real test run and caps in GEN."""
    return direct_deploy(
        CONTRACT,
        "",  # linked ProphetGrid address falls back to the accepted studionet deployment
        "",  # macro feed url falls back to the pinned default
        "",  # market depth feed url falls back to the pinned default
        40 * GEN,  # per tranche epoch cap
        10 ** 16,  # routing bond, 0.01 GEN
        600,  # subscription window seconds
        900,  # challenge window seconds
        1800,  # audit timeout seconds
    )


def at(direct_vm, timestamp: str) -> None:
    """Point gl.message_raw['datetime'] at a fixed instant for window tests."""
    gl = sys.modules.get("genlayer.gl")
    assert gl is not None, "the contract module must be imported before setting time"
    gl.message_raw["datetime"] = timestamp


def mock_feeds(direct_vm, macro=MACRO_JSON, depth=DEPTH_JSON) -> None:
    direct_vm.mock_web(r"query1\.finance\.yahoo\.com", {"status": 200, "body": macro})
    direct_vm.mock_web(r"raw\.githubusercontent\.com.*prophet-grid.*", {"status": 200, "body": depth})


class MockGrid:
    """In memory stand in for the live ProphetGrid market making book.

    It records the shares each leg acquires, then serves a preview_payout that
    reconciles the syndicate position at the configured payout ratio, so a test
    can drive neutral, gain, or loss reconciliation without a real chain.
    """

    def __init__(self, ratio_bps=10000, status="FINALIZED", market_count=5):
        self.ratio_bps = ratio_bps
        self.status = status
        self.market_count = market_count
        self.shares = {}

    def proxy(self):
        return _GridProxy(self)


class _GridProxy:
    def __init__(self, grid):
        self._grid = grid

    def view(self):
        return _GridView(self._grid)

    def emit(self, *args, **kwargs):
        return _GridWrite(self._grid, int(kwargs.get("value", 0) or 0))

    def emit_transfer(self, *args, **kwargs):
        return None


class _GridView:
    def __init__(self, grid):
        self._grid = grid

    def get_market_count(self):
        return self._grid.market_count

    def preview_payout(self, market_id, account):
        legs = self._grid.shares.get(str(market_id), {"YES": 0, "NO": 0})
        principal = legs["YES"] + legs["NO"]
        finalized = self._grid.status == "FINALIZED"
        estimated = principal * self._grid.ratio_bps // 10000 if finalized else 0
        refund = principal if self._grid.status in ("REFUNDED", "OPEN") else 0
        return json.dumps({"market_id": str(market_id), "yes_shares": legs["YES"], "no_shares": legs["NO"],
                           "principal": principal, "estimated_payout": estimated, "refund_amount": refund,
                           "status": self._grid.status})


class _GridWrite:
    def __init__(self, grid, value):
        self._grid = grid
        self._value = value

    def acquire_outcome_shares_payable(self, market_id, side):
        book = self._grid.shares.setdefault(str(market_id), {"YES": 0, "NO": 0})
        book[side] = book.get(side, 0) + self._value
        return json.dumps({"accepted": True, "market_id": str(market_id), "side": side, "shares": book[side]})

    def claim_market_winnings(self, market_id):
        return json.dumps({"payout_pgt": str(self._value), "market_id": str(market_id)})

    def claim_refund(self, market_id):
        return json.dumps({"refunded_pgt": str(self._value), "market_id": str(market_id)})


def install_grid(grid):
    """Route the contract's gl.get_contract_at to a mock grid for every address."""
    gl = sys.modules.get("genlayer.gl")
    assert gl is not None, "the contract module must be imported before installing the grid mock"
    gl.get_contract_at = lambda address: grid.proxy()
    return grid


def deploy_and_reconcile(syndicate, epoch_id=1, market_id="5", ratio_bps=10000, status="FINALIZED"):
    """Deploy the routed capital into a mock grid then reconcile it back."""
    install_grid(MockGrid(ratio_bps=ratio_bps, status=status))
    state = state_for(syndicate, epoch_id)
    routed = int(state["derived_active_deployment_amount"])
    yes_leg = routed // 2
    no_leg = routed - yes_leg
    syndicate.deploy_routed_capital_to_prophet_grid(epoch_id, market_id, yes_leg, no_leg)
    return json.loads(syndicate.reconcile_prophet_grid_position(epoch_id))


def payload(value) -> dict:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


@pytest.fixture
def syndicate(direct_deploy):
    return deploy(direct_deploy)


@pytest.fixture
def clock():
    """A fixed epoch clock. Windows are always measured against these instants."""
    return {
        "open": "2026-11-01T12:00:00Z",
        "closed": "2026-11-01T12:10:01Z",
        "audited": "2026-11-01T12:11:00Z",
        "challenge_open": "2026-11-01T12:20:00Z",
        "settle": "2026-11-01T12:30:01Z",
        "timed_out": "2026-11-01T12:50:01Z",
    }


def state_for(contract, epoch_id) -> dict:
    """Read one epoch back through the summary view, the desk's single source."""
    rows = payload(contract.get_custody_accounting_summary())["epochs"]
    for row in rows:
        if row["epoch_id"] == str(int(epoch_id)):
            return row
    raise AssertionError("epoch %s is missing from the summary" % epoch_id)


def last_state(contract) -> dict:
    return payload(contract.get_custody_accounting_summary())["epochs"][-1]


def split_receipt(value) -> list:
    """Write methods answer with a short colon separated receipt."""
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return str(value).split(":")
