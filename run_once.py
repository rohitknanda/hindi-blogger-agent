"""
run_once.py
-----------
Runs exactly ONE article generation + publish cycle.
Used by GitHub Actions on each scheduled trigger.

Usage:
    python run_once.py                       # auto round-robin category
    python run_once.py --category science    # force a specific category
    python run_once.py --category technology
    python run_once.py --category automobile
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Pre-flight check
if not os.getenv("GEMINI_API_KEY"):
    print("ERROR: GEMINI_API_KEY is missing from environment / secrets")
    sys.exit(1)

import agent  # noqa: E402

parser = argparse.ArgumentParser(description="Run one Hindi blog content cycle")
parser.add_argument(
    "--category",
    choices=["science", "technology", "automobile"],
    default=None,
    help="Force a specific category (default: auto round-robin)",
)
args = parser.parse_args()

if args.category:
    agent.cat_index = agent.CATEGORIES.index(args.category)

success = agent.run_cycle()
sys.exit(0 if success else 1)
