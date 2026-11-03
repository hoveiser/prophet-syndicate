"""Quick account check. Prints only addresses and balances, never the key."""

import os
import sys

from dotenv import load_dotenv
from web3 import Web3

from genlayer_py import create_account, create_client, studionet

RPC = "https://studio.genlayer.com/api"
EXPLORER = "https://genlayer-explorer.vercel.app/api"


def load_key():
    load_dotenv(".env")
    key = os.environ.get("GENLAYER_PRIVATE_KEY", "")
    if not key:
        print("GENLAYER_PRIVATE_KEY missing from .env")
        sys.exit(2)
    return key if key.startswith("0x") else "0x" + key


def derive(master, index):
    seed = "prophet-syndicate-burner-v1:%d:%s" % (index, master.lower())
    return "0x" + Web3.keccak(text=seed).hex()[2:]


def main():
    master = load_key()
    account = create_account(master)
    client = create_client(chain=studionet, account=account)
    w3 = Web3(Web3.HTTPProvider(RPC))
    print("deployer", account.address, "balance", w3.eth.get_balance(account.address) / 10 ** 18, "GEN")
    for index, name in enumerate(("alice", "bob", "carol"), start=1):
        burner = create_account(derive(master, index))
        print(name, burner.address, "balance", w3.eth.get_balance(burner.address) / 10 ** 18, "GEN")
    linked = "0x31C53838fF49168128D772625e34d3D2F7e11116"
    try:
        print("linked prophet grid code size", len(w3.eth.get_code(linked)))
    except Exception as exc:
        print("linked probe failed", type(exc).__name__, str(exc)[:120])


if __name__ == "__main__":
    main()
