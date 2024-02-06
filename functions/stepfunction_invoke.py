import json
import os
import boto3


def lambda_handler(event, context):
    """
    Gets Image Builder pipeline status and invokes Step Function upon successful release of new AMI.
    """
    # Extract the image ID from the SNS message
    sns_message = json.loads(event["Records"][0]["Sns"]["Message"])
    image_id = sns_message["outputResources"]["amis"][0]["image"]
    image_status = sns_message["state"]["status"]
    image_status_reason = sns_message["state"].get("reason")
    # prepare cluster filter tags
    tags_json = os.environ["TAGS"]
    tags = json.loads(tags_json)
    if image_status == "AVAILABLE":
        # Start the Step Function
        step_function_arn = os.environ["SFARN"]
        step_function_input = {"image_id": image_id, "tags": tags}
        client = boto3.client("stepfunctions")
        response = client.start_execution(
            stateMachineArn=step_function_arn, input=json.dumps(step_function_input)
        )
        # Return the Step Function execution ARN
        return response["executionArn"]
    return image_status_reason
