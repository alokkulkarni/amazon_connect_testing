# Lambda Regression Testing with LocalStack

This module provides automated regression testing for AWS Lambda functions using `testcontainers-localstack`. 
It spins up a Docker container with LocalStack to emulate AWS services (Lambda, DynamoDB, S3) locally, deploying and invoking your Lambda functions against defined test cases.

## Prerequisites

*   Docker Desktop (must be running)
*   Python 3.8+

## Setup

1.  Navigate to this directory:
    ```bash
    cd amazon_connect_testing/lambda_testing
    ```
2.  (Optional) Create a `.env` file if you have specific environment variables to load, though the test script mocks AWS credentials for LocalStack.

## Running Tests

Run the provided shell script:

```bash
./run_lambda_tests.sh
```

This script will:
1.  Create a virtual environment.
2.  Install dependencies (`pytest`, `testcontainers`, `boto3`, etc.).
3.  Start a LocalStack container.
4.  Execute tests defined in `lambda_test_cases.json`.

## Configuration

### `lambda_test_cases.json`

Define your test scenarios here. Each test case includes:

*   **function_name**: Name of the Lambda function.
*   **handler**: The handler function (e.g., `lambda_function.lambda_handler`).
*   **setup**: Resources to create before the test (S3 buckets, DynamoDB tables).
*   **trigger_event**: The JSON payload to invoke the Lambda with.
*   **validations**: Expected outcomes, including:
    *   `lambda_response`: Expected status code and body.
    *   `dynamodb_item`: Check if a specific item exists in a DynamoDB table.

### `sample_lambda.py`

This is a placeholder for your actual Lambda code. In a real scenario, you would point the test script to your actual Lambda source files.

## Troubleshooting

*   **Docker not running**: Ensure Docker Desktop is started.
*   **Container startup failure**: Check if ports 4566 are available or if Docker has enough resources.
