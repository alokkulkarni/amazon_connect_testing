#!/bin/bash
echo "Setting up Lambda Regression Test Environment..."

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

echo "Running Lambda Regression Tests with LocalStack..."
# We use pytest to run the test script
# -s allows stdout to be seen (for print statements)
# -v for verbose output
pytest -s -v test_lambda_localstack.py

EXIT_CODE=$?

deactivate

if [ $EXIT_CODE -eq 0 ]; then
    echo "Lambda Regression Tests Passed!"
else
    echo "Lambda Regression Tests Failed!"
fi

exit $EXIT_CODE
