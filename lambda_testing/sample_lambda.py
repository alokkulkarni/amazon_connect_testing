import json
import os
import boto3

def lambda_handler(event, context):
    """
    Simulates processing an S3 event and writing to DynamoDB.
    """
    print("Received event: " + json.dumps(event, indent=2))

    # Get the object from the event
    try:
        bucket = event['Records'][0]['s3']['bucket']['name']
        key = event['Records'][0]['s3']['object']['key']
    except KeyError:
        return {
            'statusCode': 400,
            'body': json.dumps('Error: Invalid S3 event structure')
        }

    table_name = os.environ.get('TABLE_NAME')
    
    if not table_name:
        return {
            'statusCode': 500,
            'body': json.dumps('Error: TABLE_NAME environment variable not set')
        }

    # Use DynamoDB client (in LocalStack, endpoint is usually handled by environment/credentials)
    dynamodb = boto3.client('dynamodb')

    try:
        dynamodb.put_item(
            TableName=table_name,
            Item={
                'file_id': {'S': key},
                'bucket': {'S': bucket},
                'status': {'S': 'PROCESSED'},
                'timestamp': {'S': '2023-01-01T12:00:00Z'}
            }
        )
        return {
            'statusCode': 200,
            'body': json.dumps('File processed successfully')
        }
    except Exception as e:
        print(e)
        print(f"Error putting item in table {table_name}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error processing file: {str(e)}')
        }

def lambda_handler_simple(event, context):
    """
    Simple greeting function.
    """
    print("Received event: " + json.dumps(event, indent=2))
    name = event.get('name', 'World')
    prefix = os.environ.get('GREETING_PREFIX', 'Hi')
    
    return {
        'statusCode': 200,
        'body': json.dumps(f"{prefix}, {name}!")
    }
