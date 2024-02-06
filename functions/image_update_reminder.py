import traceback
import os
import json
from datetime import datetime
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Configure logging
logger = Logger(service="Image update reminder", level="INFO")
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


def get_latest_image(region, image_pipeline_arn):
    """
    Returns the latest available image from Image Builder.
    """
    imagebuilder = boto3.client("imagebuilder", region_name=region)
    response = imagebuilder.list_image_pipeline_images(
        maxResults=15, imagePipelineArn=image_pipeline_arn
    )
    pipeline_response = imagebuilder.get_image_pipeline(
        imagePipelineArn=image_pipeline_arn
    )
    image_recipe_arn = pipeline_response.get("imagePipeline").get("imageRecipeArn")
    recipe_response = imagebuilder.get_image_recipe(imageRecipeArn=image_recipe_arn)
    image_summary_list = response["imageSummaryList"]
    if not image_summary_list:
        return "No available images found"
    available_images = [
        image for image in image_summary_list if image["state"]["status"] == "AVAILABLE"
    ]
    latest_image = max(available_images, key=lambda x: x["version"])
    current_pipeline_parent_ami = recipe_response.get("imageRecipe").get("parentImage")
    return latest_image, current_pipeline_parent_ami


def get_image_parameter_info(ssm_parameter_name, region):
    """
    Returns latest modified date of SSM parameter containing parent image information
    """
    ssm = boto3.client("ssm", region_name=region)
    response = ssm.get_parameters(Names=[ssm_parameter_name])
    if not response["Parameters"]:
        raise Exception(f"SSM parameter {ssm_parameter_name} not found")
    parameter_info = response["Parameters"][0]
    return parameter_info


def get_parent_image_info(region, stack_name):
    """
    Returns the current StackVersion from the CloudFormation stack output.
    """
    cloudformation = boto3.client("cloudformation", region_name=region)
    stacks = []
    next_token = None
    while True:
        if next_token:
            response = cloudformation.describe_stacks(
                StackName=stack_name, NextToken=next_token
            )
        else:
            response = cloudformation.describe_stacks(StackName=stack_name)
        stacks.extend(response["Stacks"])
        next_token = response.get("NextToken")
        if not next_token:
            break
    stack_parameters = response["Stacks"][0]["Parameters"]
    latest_eks_optimized_ami = None
    for parameter in stack_parameters:
        if parameter["ParameterKey"] == "LatestEKSOptimizedAMI":
            latest_eks_optimized_ami = parameter["ParameterValue"]
    return latest_eks_optimized_ami


def publish_sns_message(message, topic_arn):
    """
    Publishes a SNS message containing information regarding EC2 Image Builder parent image.
    """
    # Get boto3 client
    sns = boto3.client("sns")
    response = sns.publish(TopicArn=topic_arn, Message=json.dumps(message, indent=2))
    return response


@handle_errors
def lambda_handler(event, context):
    """
    Lambda function entry point.
    """
    # Get environment variables
    region = os.environ.get("REGION", "")
    stack_name = os.environ.get("STACK_NAME", "")
    image_pipeline_arn = os.environ.get("IMAGE_PIPELINE_ARN", "")
    sns_topic_arn = os.environ.get("SNS_Topic_ARN", "")
    # Get the latest image from Image Builder
    latest_image, parent_image_id = get_latest_image(region, image_pipeline_arn)
    image_creation_date = datetime.strptime(
        latest_image["dateCreated"], "%Y-%m-%dT%H:%M:%S.%f%z"
    )
    # Get the last modified date of the SSM parameter
    ssm_parameter_name = get_parent_image_info(region, stack_name)
    parameter_info = get_image_parameter_info(ssm_parameter_name, region)
    ssm_last_modified_date = parameter_info["LastModifiedDate"]
    # Compare dates and update stack
    if ssm_last_modified_date > image_creation_date:
        message = {
            "Message": "A new version of the parent image for your piprline is available",
            "Parent image SSM Parameter path": parameter_info["Name"],
            "New parent image AMI ID": parameter_info["Value"],
            "Parent image last modified date": ssm_last_modified_date.strftime(
                "%Y-%m-%dT%H:%M:%S.%f%z"
            ),
            "Current parent image AMI ID": parent_image_id,
            "Image Pipeline last image build date": image_creation_date.strftime(
                "%Y-%m-%dT%H:%M:%S.%f%z"
            ),
            "Image Pipeline ARN": image_pipeline_arn,
        }
        response = publish_sns_message(message, sns_topic_arn)
    else:
        message = {
            "Message": "Parent image is up to date",
            "Parent image SSM Parameter path": parameter_info["Name"],
            "New parent image AMI ID": parameter_info["Value"],
            "Parent image last modified date": ssm_last_modified_date.strftime(
                "%Y-%m-%dT%H:%M:%S.%f%z"
            ),
            "Current parent image AMI ID": parent_image_id,
            "Image Pipeline last image build date": image_creation_date.strftime(
                "%Y-%m-%dT%H:%M:%S.%f%z"
            ),
            "Image Pipeline ARN": image_pipeline_arn,
        }
        response = publish_sns_message(message, sns_topic_arn)
    # Return response
    return response
