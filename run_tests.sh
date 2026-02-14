#!/bin/bash
# Helper script to run tests locally

# python-dotenv will automatically load variables from .env if it exists
# No need to manually source .env here

# Run tests with -s to show stdout (logs)
pytest -s test_voice_flows.py
