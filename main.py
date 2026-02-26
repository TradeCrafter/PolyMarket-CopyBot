#!/usr/bin/env python3
"""
Polymarket Copy Trading Bot - Entry Point

Usage:
    python main.py                          # Use config.json
    python main.py --config my_config.json  # Use custom config
    python main.py --env                    # Use environment variables
    python main.py --dry-run                # Force dry run mode
"""
import sys
import os
import asyncio
import argparse
from pathlib import Path

# Ensure project root is in Python path (fixes Windows imports)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import BotConfig, WalletConfig
from src.bot import CopyTradingBot


def parse_args():
    parser = argparse.ArgumentParser(description="Polymarket Copy Trading Bot")
    parser.add_argument(
        "--config", "-c",
        default="config.json",
        help="Path to config JSON file (default: config.json)",
    )
    parser.add_argument(
        "--env",
        action="store_true",
        help="Load config from environment variables instead of file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry run mode (no real trades)",
    )
    parser.add_argument(
        "--wallet", "-w",
        action="append",
        help="Add target wallet address (can be used multiple times)",
    )
    parser.add_argument(
        "--ratio", "-r",
        type=float,
        default=1.0,
        help="Default copy ratio for CLI-added wallets (default: 1.0)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Load config
    if args.env:
        config = BotConfig.from_env()
    elif Path(args.config).exists():
        config = BotConfig.from_file(args.config)
    else:
        print(f"Config file '{args.config}' not found.")
        print("Create one from config.example.json or use --env flag.")
        print("\nQuick start:")
        print(f"  cp config.example.json config.json")
        print(f"  # Edit config.json with your settings")
        print(f"  python main.py")
        sys.exit(1)

    # Override with CLI args
    if args.dry_run:
        config.dry_run = True

    if args.wallet:
        for addr in args.wallet:
            config.target_wallets.append(
                WalletConfig(address=addr, copy_ratio=args.ratio)
            )

    # Validation
    if not config.target_wallets:
        print("❌ No target wallets configured!")
        print("Add wallets via config.json, env vars, or --wallet flag.")
        sys.exit(1)

    if not config.dry_run and not config.private_key:
        print("⚠️  No private key configured. Switching to DRY RUN mode.")
        config.dry_run = True

    # Run bot
    bot = CopyTradingBot(config)

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")


if __name__ == "__main__":
    main()
