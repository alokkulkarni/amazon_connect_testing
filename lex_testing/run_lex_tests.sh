#!/bin/bash

# Configuration
# Ensure you have your AWS credentials configured or passed as environment variables
# export AWS_ACCESS_KEY_ID=...
# export AWS_SECRET_ACCESS_KEY=...
# export LEX_BOT_ID=... (Optional if defined in test_cases.json)
# export LEX_BOT_ALIAS_ID=... (Optional if defined in test_cases.json)

echo "----------------------------------------------------------------"
echo "  AMAZON LEX REGRESSION TESTING"
echo "----------------------------------------------------------------"

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo "Error: pytest is not installed. Please run 'pip install pytest boto3 python-dotenv'"
    exit 1
fi

# Run the tests using pytest
# -s: Capture stdout/stderr (needed for print statements)
# -v: Verbose output
echo "Running tests from lex_testing/test_lex_bots.py..."
pytest -s -v lex_testing/test_lex_bots.py

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "----------------------------------------------------------------"
    echo "  ✅ ALL LEX TESTS PASSED"
    echo "----------------------------------------------------------------"
else
    echo "----------------------------------------------------------------"
    echo "  ❌ SOME LEX TESTS FAILED"
    echo "----------------------------------------------------------------"
fi

exit $EXIT_CODE
