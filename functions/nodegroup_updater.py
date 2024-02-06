import traceback
import json
from datetime import datetime
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Configure logging
logger = Logger(service="Node group updater", level="INFO")
# Configure Boto3 retry mode with max_attempts
boto3_config = Config(retries={"mode": "standard", "max_attempts": 10})


def handle_errors(func):
    """
    A decorator to handle exceptions that might be raised by the wrapped function.
    """

    @logger.inject_lambda_context
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            logger.error(
                "ClientError occurred in function %s: %s - %s",
                traceback.extract_stack()[-2].name,
                error_code,
                error_message,
            )
            return {
                "status": "error",
                "error_code": error_code,
                "message": error_message,
            }
        except BotoCoreError:
            logger.error(
                "BotoCoreError occurred in function %s.",
                traceback.extract_stack()[-2].name,
            )
            return {"status": "error", "message": "An AWS SDK error occurred."}
        except Exception as e:
            logger.error(
                "Unexpected error occurred in function %s: %s",
                traceback.extract_stack()[-2].name,
                str(e),
            )
            return {
                "status": "error",
                "message": f"An unexpected error occurred: {str(e)}",
            }

    return wrapper


def prepare_response(response):
    """
    Prepares the response dictionary.
    Parameters:
    response (dict): The response dictionary potentially containing datetime objects.
    Returns:
    dict: The processed dictionary with datetime objects converted to ISO 8601 format string
    """
    if isinstance(response["update"]["createdAt"], datetime):
        response["update"]["createdAt"] = response["update"]["createdAt"].isoformat()
    return response


# Create a global boto3 EKS client
CLIENT = boto3.client("eks")


def update_nodegroup(event):
    """
    Updates the nodegroup using the provided event details.
    """
    # Extract values from the input event
    launch_template = event.get("launchTemplate", {})
    cluster_name = event.get("clusterName")
    nodegroup_name = event.get("nodegroupName")
    version = event.get("version")
    # Call the update_nodegroup_version method
    response = CLIENT.update_nodegroup_version(
        clusterName=cluster_name,
        nodegroupName=nodegroup_name,
        launchTemplate={
            "version": version,
            "id": launch_template.get("id"),
        },
    )
    return response


@handle_errors
def lambda_handler(event, context):
    """
    Lambda function entry point.
    """
    response = update_nodegroup(event)
    prepared_response = prepare_response(response)
    return {"status": "complete", "response": prepared_response}
