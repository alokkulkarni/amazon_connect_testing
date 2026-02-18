import boto3
import zipfile
import io
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

def update_lambda():
    lambda_client = boto3.client('lambda', region_name='us-east-1')
    function_name = 'ChimeHandler' # Replace with your actual function name if different

    # Resolve handler source path relative to this file so the script can be
    # called from any working directory.
    handler_path = os.path.join(_HERE, 'chime_handler_lambda.py')

    # Create zip file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.write(handler_path, arcname='lambda_function.py')
    
    zip_buffer.seek(0)
    
    print(f"Updating Lambda function '{function_name}'...")
    try:
        response = lambda_client.update_function_code(
            FunctionName=function_name,
            ZipFile=zip_buffer.read()
        )
        print("Lambda function updated successfully.")
        print(f"New Code Size: {response['CodeSize']} bytes")
        print(f"Last Modified: {response['LastModified']}")
    except Exception as e:
        print(f"Error updating Lambda: {e}")
        print("Please ensure the function name is correct and you have permissions.")

if __name__ == "__main__":
    update_lambda()
