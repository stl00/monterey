from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from . import config
from .aggregate import collect, render
from .connectors import build
from .fx import FxRates
from .store import Store


def cmd_sync(args) -> None:
    sources = args.only.split(",") if args.only else config.enabled_sources()
    snap = collect([build(s) for s in sources], FxRates.fetch(config.base_currency()))
    if not args.no_save:
        Store(config.data_dir() / "portfolio.db").save(snap)
    if args.json:
        print(json.dumps({"taken_at": snap.taken_at, "base_ccy": snap.base_ccy, "total": snap.total,
                          "holdings": [{**asdict(h), "value_base": v} for h, v in snap.rows],
                          "status": snap.status, "warnings": snap.warnings}, ensure_ascii=False, indent=2))
    else:
        print(render(snap))


def cmd_history(args) -> None:
    for row in Store(config.data_dir() / "portfolio.db").history():
        print(f"{row['taken_at']}  {row['total']:>16,.0f} {row['base_ccy']}")


def cmd_schwab_login(args) -> None:
    from .connectors.schwab import SchwabClient
    client = SchwabClient.from_env()
    print("1. Open this URL, log in to Schwab and approve access:\n")
    print("   " + client.authorize_url() + "\n")
    print("2. The browser then lands on a page that fails to load. Copy that page's full URL.")
    client.exchange_redirect(input("\nPaste the URL here: ").strip())
    print("Logged in. Valid for 7 days.")


def main(argv=None) -> None:
    config.load_dotenv()
    p = argparse.ArgumentParser(prog="portfolio", description="Aggregate holdings across accounts")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("sync", help="Fetch all sources, print the summary and save a snapshot")
    s.add_argument("--only", help="Comma-separated sources, e.g. okx,futu")
    s.add_argument("--no-save", action="store_true", help="Do not write a snapshot")
    s.add_argument("--json", action="store_true", help="Print JSON instead of the text summary")
    s.set_defaults(func=cmd_sync)

    sub.add_parser("history", help="Total per saved snapshot").set_defaults(func=cmd_history)
    sub.add_parser("schwab-login", help="Authorize Schwab (needed every 7 days)").set_defaults(func=cmd_schwab_login)

    args = p.parse_args(argv)
    args.func(args)
