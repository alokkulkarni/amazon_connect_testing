"""
sample_lambda.py – Sample Lambda handlers for local regression testing.

All handlers are packaged into lambda_function.py inside the deployment ZIP.
Each handler exercises a different combination of AWS services / behaviours
so the test suite can validate a broad range of scenarios.
"""

import json
import os
import boto3


# ---------------------------------------------------------------------------
# TC-001  S3 event → DynamoDB writer
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    Process an S3 PutObject event and write file metadata to DynamoDB.
    Env var: TABLE_NAME (required)
    """
    print("Received event: " + json.dumps(event, indent=2))

    try:
        bucket = event["Records"][0]["s3"]["bucket"]["name"]
        key    = event["Records"][0]["s3"]["object"]["key"]
    except (KeyError, IndexError):
        return {"statusCode": 400, "body": json.dumps("Error: Invalid S3 event structure")}

    table_name = os.environ.get("TABLE_NAME")
    if not table_name:
        return {"statusCode": 500, "body": json.dumps("Error: TABLE_NAME environment variable not set")}

    dynamodb = boto3.client("dynamodb")
    try:
        dynamodb.put_item(
            TableName=table_name,
            Item={
                "file_id":   {"S": key},
                "bucket":    {"S": bucket},
                "status":    {"S": "PROCESSED"},
                "timestamp": {"S": "2024-01-01T12:00:00Z"},
            },
        )
        return {"statusCode": 200, "body": json.dumps("File processed successfully")}
    except Exception as exc:
        print(f"DynamoDB error: {exc}")
        return {"statusCode": 500, "body": json.dumps(f"Error processing file: {str(exc)}")}


# ---------------------------------------------------------------------------
# TC-002  Simple greeting function
# ---------------------------------------------------------------------------

def lambda_handler_simple(event, context):
    """
    Return a greeting string.
    Env var: GREETING_PREFIX (optional, default 'Hi')
    """
    print("Received event: " + json.dumps(event, indent=2))
    name   = event.get("name", "World")
    prefix = os.environ.get("GREETING_PREFIX", "Hi")
    return {"statusCode": 200, "body": json.dumps(f"{prefix}, {name}!")}


# ---------------------------------------------------------------------------
# TC-003  Lambda that writes a result to S3
# ---------------------------------------------------------------------------

def lambda_handler_s3_writer(event, context):
    """
    Reads 'bucket', 'key', and 'content' from the event and writes to S3.
    Returns the S3 URI of the written object.
    """
    print("Received event: " + json.dumps(event, indent=2))
    bucket  = event.get("bucket")
    key     = event.get("key")
    content = event.get("content", "")

    if not bucket or not key:
        return {"statusCode": 400, "body": json.dumps("Error: 'bucket' and 'key' are required")}

    s3 = boto3.client("s3")
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
        uri = f"s3://{bucket}/{key}"
        return {"statusCode": 200, "body": json.dumps(f"Written to {uri}")}
    except Exception as exc:
        print(f"S3 error: {exc}")
        return {"statusCode": 500, "body": json.dumps(f"S3 write error: {str(exc)}")}


# ---------------------------------------------------------------------------
# TC-004  Environment variable echo
# ---------------------------------------------------------------------------

def lambda_handler_env_echo(event, context):
    """
    Return all environment variables as a JSON body for inspection.
    Useful for validating that all required env vars are injected.
    """
    env_vars = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    return {"statusCode": 200, "body": json.dumps(env_vars)}


# ---------------------------------------------------------------------------
# TC-005  Invalid event structure (returns 400)
# ---------------------------------------------------------------------------
# Reuses lambda_handler above – sending a malformed event exercises the
# KeyError / 400 path without a separate handler.


# ---------------------------------------------------------------------------
# TC-006  Missing required env var (returns 500)
# ---------------------------------------------------------------------------
# Reuses lambda_handler above – deploying without TABLE_NAME exercises the
# env-var-missing / 500 path.


# ---------------------------------------------------------------------------
# TC-007  Intentional unhandled exception (FunctionError)
# ---------------------------------------------------------------------------

def lambda_handler_raise(event, context):
    """
    Always raises an unhandled exception – used to test FunctionError detection.
    """
    raise RuntimeError("Intentional test exception – this is expected in TC-007")


# ---------------------------------------------------------------------------
# TC-008  DynamoDB conditional writer (idempotency check)
# ---------------------------------------------------------------------------

def lambda_handler_conditional_write(event, context):
    """
    Write an item to DynamoDB only if it does not already exist (attribute_not_exists).
    Returns 409 if the item already exists.
    Env var: TABLE_NAME (required)
    """
    print("Received event: " + json.dumps(event, indent=2))
    table_name = os.environ.get("TABLE_NAME")
    if not table_name:
        return {"statusCode": 500, "body": json.dumps("Error: TABLE_NAME not set")}

    item_id = event.get("item_id")
    value   = event.get("value", "")

    if not item_id:
        return {"statusCode": 400, "body": json.dumps("Error: 'item_id' is required")}

    dynamodb = boto3.client("dynamodb")
    try:
        dynamodb.put_item(
            TableName=table_name,
            Item={
                "item_id": {"S": item_id},
                "value":   {"S": value},
            },
            ConditionExpression="attribute_not_exists(item_id)",
        )
        return {"statusCode": 201, "body": json.dumps(f"Item {item_id} created")}
    except dynamodb.exceptions.ConditionalCheckFailedException:
        return {"statusCode": 409, "body": json.dumps(f"Item {item_id} already exists")}
    except Exception as exc:
        return {"statusCode": 500, "body": json.dumps(str(exc))}

