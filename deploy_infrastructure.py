import boto3
import json
import time
import os
import zipfile

# Configuration
DYNAMODB_TABLE_NAME = "VoiceTestState"
LAMBDA_FUNCTION_NAME = "ChimeSMAHandler" # Verify this matches your actual Lambda name
REGION = "us-east-1" # Chime SMA is usually in us-east-1, but check your setup

def create_dynamodb_table():
    dynamodb = boto3.client('dynamodb', region_name=REGION)
    try:
        dynamodb.create_table(
            TableName=DYNAMODB_TABLE_NAME,
            KeySchema=[
                {'AttributeName': 'conversation_id', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'conversation_id', 'AttributeType': 'S'}
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        print(f"Creating DynamoDB table {DYNAMODB_TABLE_NAME}...")
        waiter = dynamodb.get_waiter('table_exists')
        waiter.wait(TableName=DYNAMODB_TABLE_NAME)
        print(f"Table {DYNAMODB_TABLE_NAME} created successfully.")
    except dynamodb.exceptions.ResourceInUseException:
        print(f"Table {DYNAMODB_TABLE_NAME} already exists.")

def update_lambda_code():
    lambda_client = boto3.client('lambda', region_name=REGION)
    
    # Create deployment package
    zip_filename = 'lambda_deploy.zip'
    with zipfile.ZipFile(zip_filename, 'w') as zip_file:
        zip_file.write('chime_handler_lambda.py')
    
    with open(zip_filename, 'rb') as f:
        zip_content = f.read()
    
    try:
        # Get the actual function name if different (e.g., from a list)
        functions = lambda_client.list_functions()
        target_arn = None
        for func in functions['Functions']:
            # Simple heuristic: look for "Chime" or the name we used before
            if "Chime" in func['FunctionName'] or "SMA" in func['FunctionName']:
                target_arn = func['FunctionArn']
                print(f"Found Lambda function: {func['FunctionName']}")
                break
        
        if target_arn:
            print(f"Updating code for Lambda: {target_arn}")
            lambda_client.update_function_code(
                FunctionName=target_arn,
                ZipFile=zip_content
            )
            print("Lambda code updated successfully.")
        else:
            print("WARNING: Could not find a Lambda function with 'Chime' or 'SMA' in the name. Please update manually.")
            
    except Exception as e:
        print(f"Error updating Lambda: {e}")
    finally:
        if os.path.exists(zip_filename):
            os.remove(zip_filename)

if __name__ == "__main__":
    create_dynamodb_table()
    update_lambda_code()
