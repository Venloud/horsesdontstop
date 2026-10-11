"""Read-only Alpaca PAPER account connectivity. No orders submitted."""
import argparse
import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args()
    load_dotenv()
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("Set paper credentials in .env, never live credentials")
    client = TradingClient(key, secret, paper=True, url_override="https://paper-api.alpaca.markets")
    account = client.get_account()
    clock = client.get_clock()
    print(f"PAPER account: {account.status}; equity: {account.equity}; market open: {clock.is_open}")
    print("Read-only check complete. No orders submitted.")

if __name__ == "__main__":
    main()
