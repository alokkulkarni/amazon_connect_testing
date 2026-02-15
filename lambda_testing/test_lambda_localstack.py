import json
import os
import time
import zipfile
import pytest
import boto3
from testcontainers.localstack import LocalStackContainer
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TEST_CASES_FILE = 'lambda_test_cases.json'
LAMBDA_CODE_FILE = 'sample_lambda.py' # The python file containing our lambda functions
LAMBDA_ZIP_FILE = 'lambda_function.zip'

def create_lambda_zip(source_file, zip_name):
    """Zips the lambda code for deployment."""
    with zipfile.ZipFile(zip_name, 'w') as zf:
        # We rename the source file to lambda_function.py inside the zip for standard handler naming
        zf.write(source_file, arcname='lambda_function.py')

def load_test_cases():
    with open(TEST_CASES_FILE, 'r') as f:
        return json.load(f)

@pytest.fixture(scope="module")
def localstack():
    """Spins up a LocalStack container for the duration of the tests."""
    # We need Lambda, DynamoDB, S3, IAM, CloudWatch Logs
    with LocalStackContainer(image="localstack/localstack:3.0.0") as localstack:
        yield localstack

@pytest.fixture(scope="module")
def aws_clients(localstack):
    """Returns boto3 clients configured to talk to the LocalStack container."""
    endpoint_url = localstack.get_url()
    region_name = "us-east-1"
    aws_access_key_id = "test"
    aws_secret_access_key = "test"

    return {
        'lambda': boto3.client(
            'lambda', endpoint_url=endpoint_url, region_name=region_name,
            aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key
        ),
        's3': boto3.client(
            's3', endpoint_url=endpoint_url, region_name=region_name,
            aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key
        ),
        'dynamodb': boto3.client(
            'dynamodb', endpoint_url=endpoint_url, region_name=region_name,
            aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key
        ),
        'iam': boto3.client(
            'iam', endpoint_url=endpoint_url, region_name=region_name,
            aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key
        )
    }

@pytest.fixture(scope="module")
def lambda_deployment_package():
    """Creates the deployment package."""
    create_lambda_zip(LAMBDA_CODE_FILE, LAMBDA_ZIP_FILE)
    yield LAMBDA_ZIP_FILE
    if os.path.exists(LAMBDA_ZIP_FILE):
        os.remove(LAMBDA_ZIP_FILE)

def setup_resources(clients, setup_config):
    """Sets up S3 buckets and DynamoDB tables as defined in the test case."""
    # S3 Buckets
    if 's3_buckets' in setup_config:
        for bucket in setup_config['s3_buckets']:
            try:
                clients['s3'].create_bucket(Bucket=bucket)
                print(f"Created bucket: {bucket}")
            except Exception as e:
                print(f"Bucket creation failed (might exist): {e}")

    # DynamoDB Tables
    if 'dynamodb_tables' in setup_config:
        for table_def in setup_config['dynamodb_tables']:
            try:
                clients['dynamodb'].create_table(**table_def)
                print(f"Created table: {table_def['TableName']}")
            except Exception as e:
                print(f"Table creation failed (might exist): {e}")

@pytest.mark.parametrize("test_case", load_test_cases())
def test_lambda_function(localstack, aws_clients, lambda_deployment_package, test_case):
    print(f"\n--- Running Test Case: {test_case['name']} ---")

    # 1. Setup Resources (S3, DynamoDB)
    setup_resources(aws_clients, test_case.get('setup', {}))

    # 2. Deploy Lambda Function
    role_arn = "arn:aws:iam::000000000000:role/lambda-role" # Mock role
    
    function_name = test_case['function_name']
    
    # Check if function exists, delete if so to ensure fresh state
    try:
        aws_clients['lambda'].delete_function(FunctionName=function_name)
    except:
        pass

    with open(lambda_deployment_package, 'rb') as f:
        zip_content = f.read()

    aws_clients['lambda'].create_function(
        FunctionName=function_name,
        Runtime='python3.9',
        Role=role_arn,
        Handler=test_case['handler'],
        Code={'ZipFile': zip_content},
        Timeout=test_case.get('timeout', 3),
        MemorySize=test_case.get('memory_size', 128),
        Environment={'Variables': test_case.get('environment_variables', {})}
    )
    
    # Wait for function to be active
    waiter = aws_clients['lambda'].get_waiter('function_active')
    try:
        waiter.wait(FunctionName=function_name)
    except:
        pass # LocalStack is usually instant

    # 3. Invoke Lambda
    payload = json.dumps(test_case['trigger_event'])
    
    response = aws_clients['lambda'].invoke(
        FunctionName=function_name,
        InvocationType='RequestResponse',
        Payload=payload
    )
    
    response_payload = json.loads(response['Payload'].read())
    print(f"Lambda Response: {response_payload}")

    # 4. Validations
    validations = test_case.get('validations', {})

    # Validate Response
    if 'lambda_response' in validations:
        expected_resp = validations['lambda_response']
        # Simple check: status code and body
        if 'statusCode' in expected_resp:
            assert response_payload['statusCode'] == expected_resp['statusCode']
        if 'body' in expected_resp:
            assert response_payload['body'] == expected_resp['body']
            
    # Validate DynamoDB Item
    if 'dynamodb_item' in validations:
        ddb_val = validations['dynamodb_item']
        table = ddb_val['TableName']
        key = ddb_val['Key']
        expected_item_part = ddb_val.get('ExpectedAttributeValue', {})
        
        # Fetch item
        item_resp = aws_clients['dynamodb'].get_item(TableName=table, Key=key)
        item = item_resp.get('Item')
        
        assert item is not None, f"Item not found in table {table} with key {key}"
        
        # Check expected attributes
        for k, v in expected_item_part.items():
            assert item.get(k) == v, f"Attribute {k} mismatch. Expected {v}, got {item.get(k)}"

    print(f"Test Case '{test_case['name']}' PASSED.")
