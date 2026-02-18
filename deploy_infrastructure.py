import boto3
import json
import time
import os
import zipfile
import sys
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Configuration
# FIX: ENV_NAME namespaces all resource names so dev/test/prod are isolated.
# ---------------------------------------------------------------------------
ENV_NAME            = os.environ.get('ENV_NAME', 'dev')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', f'VoiceTestState-{ENV_NAME}')
AWS_REGION          = os.environ.get('CHIME_AWS_REGION', os.environ.get('AWS_REGION', 'us-east-1'))
LAMBDA_FUNCTION_NAME = f'ChimeSMAHandler-{ENV_NAME}'
SMA_NAME             = f'ChimeAutomationSMA-{ENV_NAME}'
IAM_ROLE_NAME        = f'ChimeTestLambdaRole-{ENV_NAME}'

def create_dynamodb_table(dynamodb_client, account_id: str):
    """
    Create DynamoDB table with:
      - PAY_PER_REQUEST billing (no provisioned capacity sitting idle)
      - TTL attribute enabled on 'ttl' field
    FIX: Replaces ProvisionedThroughput with on-demand billing and adds TTL.
    """
    table_arn = f"arn:aws:dynamodb:{AWS_REGION}:{account_id}:table/{DYNAMODB_TABLE_NAME}"
    try:
        print(f"Checking DynamoDB table {DYNAMODB_TABLE_NAME}...")
        dynamodb_client.create_table(
            TableName=DYNAMODB_TABLE_NAME,
            KeySchema=[{'AttributeName': 'conversation_id', 'KeyType': 'HASH'}],
            AttributeDefinitions=[{'AttributeName': 'conversation_id', 'AttributeType': 'S'}],
            BillingMode='PAY_PER_REQUEST',
        )
        print(f"Creating DynamoDB table {DYNAMODB_TABLE_NAME}...")
        waiter = dynamodb_client.get_waiter('table_exists')
        waiter.wait(TableName=DYNAMODB_TABLE_NAME)
        print(f"Table {DYNAMODB_TABLE_NAME} created successfully.")
    except dynamodb_client.exceptions.ResourceInUseException:
        print(f"Table {DYNAMODB_TABLE_NAME} already exists.")
    except Exception as e:
        print(f"Error creating DynamoDB table: {e}")
        return None

    # Enable TTL so test items auto-expire after their 'ttl' Unix timestamp
    try:
        dynamodb_client.update_time_to_live(
            TableName=DYNAMODB_TABLE_NAME,
            TimeToLiveSpecification={'Enabled': True, 'AttributeName': 'ttl'}
        )
        print(f"TTL enabled on '{DYNAMODB_TABLE_NAME}' (attribute: ttl).")
    except Exception as e:
        print(f"Warning: Could not enable TTL: {e}")

    return table_arn

def get_or_create_iam_role(iam, account_id: str, table_arn: str):
    """
    Create the Lambda execution role with a least-privilege inline policy.
    FIX: Replaces AmazonDynamoDBFullAccess (account-wide) with a resource-scoped
         inline policy that only allows the specific test table.
    """
    print(f"Checking IAM Role {IAM_ROLE_NAME}...")
    try:
        role = iam.get_role(RoleName=IAM_ROLE_NAME)
        return role['Role']['Arn']
    except iam.exceptions.NoSuchEntityException:
        print(f"Creating IAM Role {IAM_ROLE_NAME}...")
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect":    "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action":    "sts:AssumeRole"
            }]
        }
        role = iam.create_role(
            RoleName=IAM_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy)
        )
        # Basic CloudWatch Logs execution policy
        iam.attach_role_policy(
            RoleName=IAM_ROLE_NAME,
            PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
        )
        # FIX: Resource-scoped inline DynamoDB policy (replaces AmazonDynamoDBFullAccess)
        scoped_dynamodb_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect":   "Allow",
                "Action":   [
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:DescribeTable"
                ],
                "Resource": table_arn if table_arn else f"arn:aws:dynamodb:{AWS_REGION}:{account_id}:table/{DYNAMODB_TABLE_NAME}"
            }]
        }
        iam.put_role_policy(
            RoleName=IAM_ROLE_NAME,
            PolicyName='ChimeTestLambdaDynamoDBPolicy',
            PolicyDocument=json.dumps(scoped_dynamodb_policy)
        )
        time.sleep(10)   # Allow IAM propagation
        return role['Role']['Arn']

def create_lambda_package():
    zip_filename = 'lambda_deploy.zip'
    # Use absolute path to ensure we find the file regardless of CWD
    source_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chime_handler_lambda.py')
    
    with zipfile.ZipFile(zip_filename, 'w') as zip_file:
        zip_file.write(source_file, arcname='lambda_function.py')
    with open(zip_filename, 'rb') as f:
        return f.read()

def get_or_create_lambda(lambda_client, iam_role_arn):
    print(f"Checking Lambda Function {LAMBDA_FUNCTION_NAME}...")
    zip_content = create_lambda_package()

    env_vars = {
        'DYNAMODB_TABLE_NAME': DYNAMODB_TABLE_NAME,
        'ENV_NAME':            ENV_NAME,
    }

    try:
        lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)
        print(f"Updating Lambda Code for {LAMBDA_FUNCTION_NAME}...")
        lambda_client.update_function_code(
            FunctionName=LAMBDA_FUNCTION_NAME,
            ZipFile=zip_content
        )
        print("Waiting for Lambda update...")
        waiter = lambda_client.get_waiter('function_updated')
        waiter.wait(FunctionName=LAMBDA_FUNCTION_NAME)
        lambda_client.update_function_configuration(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Environment={'Variables': env_vars}
        )
    except lambda_client.exceptions.ResourceNotFoundException:
        print(f"Creating Lambda Function {LAMBDA_FUNCTION_NAME}...")
        lambda_client.create_function(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Runtime='python3.12',
            Role=iam_role_arn,
            Handler='lambda_function.lambda_handler',
            Code={'ZipFile': zip_content},
            Environment={'Variables': env_vars},
            Timeout=30
        )
        waiter = lambda_client.get_waiter('function_active')
        waiter.wait(FunctionName=LAMBDA_FUNCTION_NAME)

    # FIX: Correct principals for Chime SDK Voice SMA invocations.
    # The SMA uses 'voiceconnector.chime.amazonaws.com' as the invoking principal.
    # We add both to cover legacy and SDK-native invocations.
    for stmt_id, principal in [
        ('ChimeVoiceConnectorInvokePermission', 'voiceconnector.chime.amazonaws.com'),
        ('ChimeSMAInvokePermission',            'chime.amazonaws.com'),
    ]:
        try:
            lambda_client.add_permission(
                FunctionName=LAMBDA_FUNCTION_NAME,
                StatementId=stmt_id,
                Action='lambda:InvokeFunction',
                Principal=principal
            )
            print(f"Added Lambda permission for principal: {principal}")
        except lambda_client.exceptions.ResourceConflictException:
            pass   # Already exists

    fn_arn = lambda_client.get_function(
        FunctionName=LAMBDA_FUNCTION_NAME
    )['Configuration']['FunctionArn']
    print(f"Lambda ARN: {fn_arn}")
    return fn_arn

def get_or_create_sma(chime, lambda_arn):
    print(f"Checking SIP Media Application {SMA_NAME}...")
    # List SMAs to find by name
    smas = chime.list_sip_media_applications()
    for sma in smas.get('SipMediaApplications', []):
        if sma['Name'] == SMA_NAME:
            print(f"Found existing SMA: {sma['SipMediaApplicationId']}")
            # Update endpoint if needed
            chime.update_sip_media_application(
                SipMediaApplicationId=sma['SipMediaApplicationId'],
                Endpoints=[{'LambdaArn': lambda_arn}]
            )
            return sma['SipMediaApplicationId']

    print(f"Creating SIP Media Application {SMA_NAME}...")
    response = chime.create_sip_media_application(
        AwsRegion=AWS_REGION,
        Name=SMA_NAME,
        Endpoints=[{'LambdaArn': lambda_arn}]
    )
    return response['SipMediaApplication']['SipMediaApplicationId']

def provision_phone_number(chime, sma_id):
    print("Checking phone numbers...")
    
    try:
        # Check existing inventory
        response = chime.list_phone_numbers()
        available_phone = None
        
        for phone in response.get('PhoneNumbers', []):
            if phone['Status'] == 'Unassigned':
                available_phone = phone['E164PhoneNumber']
                print(f"Found available unassigned number: {available_phone}")
                # We could associate it with a SIP Rule for inbound testing, but for this outbound test it's fine.
                return available_phone
            elif phone['Status'] == 'Assigned':
                 # If we find any assigned number, we might as well use it if we can't find an unassigned one
                 # But ideally we want an unassigned one to ensure we own it for this purpose
                 # But if it's assigned to THIS SMA, that's perfect.
                 # However, checking association is hard without iterating SipRules.
                 # Let's just pick any number in inventory as a fallback.
                 if not available_phone:
                     available_phone = phone['E164PhoneNumber']

        if available_phone:
            print(f"Using available number: {available_phone}")
            return available_phone

    except Exception as e:
        print(f"Error listing phone numbers: {e}")

    # Fallback: Check if CHIME_PHONE_NUMBER env var is set
    env_phone = os.environ.get('CHIME_PHONE_NUMBER')
    if env_phone:
        print(f"Using configured phone number: {env_phone}")
        return env_phone
        
    print("WARNING: No available phone number found in inventory.")
    return None

def create_sip_rule(chime, sma_id, phone_number):
    if not phone_number:
        print("Skipping SIP Rule creation (no phone number).")
        return

    print(f"Checking SIP Rule for {phone_number}...")
    rule_name = f"Rule-{phone_number.replace('+', '')}"
    
    try:
        # Check existing rules
        rules = chime.list_sip_rules()
        for rule in rules.get('SipRules', []):
            if rule['TriggerValue'] == phone_number:
                print(f"Found existing SIP Rule: {rule['SipRuleId']}")
                
                current_sma = None
                if rule.get('TargetApplications'):
                    current_sma = rule['TargetApplications'][0]['SipMediaApplicationId']
                
                if current_sma != sma_id:
                    print(f"Updating SIP Rule to point to SMA {sma_id}...")
                    chime.update_sip_rule(
                        SipRuleId=rule['SipRuleId'],
                        Name=rule_name,
                        TargetApplications=[{'SipMediaApplicationId': sma_id, 'Priority': 1}]
                    )
                return rule['SipRuleId']
        
        # Create new rule
        print(f"Creating SIP Rule for {phone_number}...")
        response = chime.create_sip_rule(
            Name=rule_name,
            TriggerType='ToPhoneNumber',
            TriggerValue=phone_number,
            TargetApplications=[{'SipMediaApplicationId': sma_id, 'Priority': 1}]
        )
        return response['SipRule']['SipRuleId']

    except Exception as e:
        print(f"Error managing SIP Rule: {e}")

def deploy():
    session       = boto3.Session(region_name=AWS_REGION)
    dynamodb      = session.client('dynamodb')
    iam           = session.client('iam')
    lambda_client = session.client('lambda')
    chime         = session.client('chime-sdk-voice')
    sts           = session.client('sts')

    account_id = sts.get_caller_identity()['Account']
    print(f"Deploying into account {account_id}, region {AWS_REGION}, env {ENV_NAME}")

    # Order: table first so its ARN is available for the IAM policy
    table_arn  = create_dynamodb_table(dynamodb, account_id)
    role_arn   = get_or_create_iam_role(iam, account_id, table_arn)
    lambda_arn = get_or_create_lambda(lambda_client, role_arn)
    sma_id     = get_or_create_sma(chime, lambda_arn)
    phone      = provision_phone_number(chime, sma_id)

    if phone:
        create_sip_rule(chime, sma_id, phone)

    output = {
        'CHIME_SMA_ID':        sma_id,
        'CHIME_PHONE_NUMBER':  phone,
        'LAMBDA_ARN':          lambda_arn,
        'DYNAMODB_TABLE':      DYNAMODB_TABLE_NAME,
        'ENV_NAME':            ENV_NAME,
    }
    with open('infrastructure_output.json', 'w') as f:
        json.dump(output, f, indent=2)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    deploy()
