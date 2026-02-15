#!/bin/bash
# Helper script to run tests locally

# python-dotenv will automatically load variables from .env if it exists
# No need to manually source .env here

# Run tests with -s to show stdout (logs)
echo "Running Voice Tests..."
pytest -s test_voice_flows.py

echo "Running Lex Bot Tests..."
pytest -s test_lex_bots.py
