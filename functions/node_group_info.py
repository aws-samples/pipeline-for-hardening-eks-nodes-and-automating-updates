import traceback
import os
import base64
import textwrap
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Configure logging
logger = Logger(service="Launch template updater", level="INFO")
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

def list_clusters(eks_client):
    """
    Lists all EKS clusters.
    Args:
        eks_client: An initialized Boto3 EKS client.
    Returns:
        A list of cluster ARNs.
    """
    paginator = eks_client.get_paginator("list_clusters")
    clusters = []
    for page in paginator.paginate():
        clusters.extend(page["clusters"])
    return clusters

def filter_clusters(eks_client, clusters, required_tags):
    """
    Retrieves node groups for clusters with the required tags.
    Args:
        ecs: An initialized Boto3 EKS client.
        clusters: A list of EKS cluster names.
        required_tags: A list of tags to filter clusters. If empty, returns node groups for all clusters.
    Returns:
        A list of node groups.
    """
    required_tags_set = (
        {(tag["Key"], tag["Value"]) for tag in required_tags} if required_tags else None
    )

    def has_required_tags(cluster_tags):
        if not required_tags_set:
            return True
        cluster_tags_set = set(cluster_tags.items())
        return required_tags_set.issubset(cluster_tags_set)

    filtered_clusters = []
    for cluster in clusters:
        cluster_summary = {}
        cluster_info = eks_client.describe_cluster(name=cluster)
        cluster = cluster_info["cluster"]
        cluster_tags = cluster["tags"]
        raw_dns = cluster["kubernetesNetworkConfig"].get("serviceIpv4Cidr", False)
        if has_required_tags(cluster_tags) and raw_dns:
            cluster_summary = {
                "name": cluster["name"],
                "cluster-ca": cluster["certificateAuthority"].get("data"),
                "endpoint": cluster["endpoint"],
                "dns": ".".join(raw_dns.split("/")[0].split(".")[:-1] + ["10"]),
                "cidr": raw_dns,
            }
            filtered_clusters.append(cluster_summary)
    return filtered_clusters

def get_node_groups(eks_client, filtered_clusters):
    """
    Gets the node groups associated with the cluster.
    Args:
        eks_client: An initialized Boto3 EKS client.
        filtered_clusters: A list of EKS Cluster names.
    Returns:
        A list of dictionaries containing node group information.
    """
    node_groups = []
    for cluster in filtered_clusters:
        paginator = eks_client.get_paginator("list_nodegroups")
        node_groups_list = []
        for page in paginator.paginate(clusterName=cluster["name"]):
            node_groups_list.extend(page["nodegroups"])
        for group in node_groups_list:
            group_info = eks_client.describe_nodegroup(
                clusterName=cluster["name"], nodegroupName=group
            )
            if (
                "launchTemplate" in group_info["nodegroup"]
                and group_info["nodegroup"].get("releaseVersion").startswith("ami")
                and group_info["nodegroup"].get("status") == "ACTIVE"
            ):
                node_group = {
                    "launchTemplate": group_info["nodegroup"].get("launchTemplate"),
                    "nodegroupName": group_info["nodegroup"].get("nodegroupName"),
                    "nodegroupArn": group_info["nodegroup"].get("nodegroupArn"),
                    "clusterName": group_info["nodegroup"].get("clusterName"),
                    "cluster-ca": cluster["cluster-ca"],
                    "endpoint": cluster["endpoint"],
                    "dns": cluster["dns"],
                    "cidr": cluster["cidr"],
                }
                node_groups.append(node_group)
    return node_groups

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

def update_launch_templates(node_groups, image_id):
    """
    Updates the launch templates of the node groups with the provided image ID.
    Args:
        eks_client: An initialized Boto3 EKS client.
        node_groups: A list of dictionaries containing node groups' information.
        image_id: The image ID to be used in the updated launch templates.
    Returns:
        A list of dictionaries containing the updated node group information.
    """
    ec2_client = boto3.client("ec2")
    region = os.environ.get("REGION", "")
    stack_name = os.environ.get("STACK_NAME", "")
    ssm_parameter_name = get_parent_image_info(region, stack_name)
    processed_launch_templates = set()
    for node_group in node_groups:
        launch_template = node_group["launchTemplate"]
        launch_template_id = launch_template["id"]
        launch_template_version = launch_template["version"]
        if "amazon-linux-2023" in ssm_parameter_name:
            user_data = textwrap.dedent(
                f"""
                ---
                apiVersion: node.eks.aws/v1alpha1
                kind: NodeConfig
                spec:
                    cluster:
                        name: {node_group["clusterName"]}
                        apiServerEndpoint: {node_group["endpoint"]}
                        certificateAuthority: {node_group["cluster-ca"]}
                        cidr: {node_group["cidr"]}
                """
            ).strip()
        elif "amazon-linux-2" in ssm_parameter_name:
            user_data = textwrap.dedent(
                f"""
                MIME-Version: 1.0
                Content-Type: multipart/mixed; boundary="==MYBOUNDARY=="
                --==MYBOUNDARY==
                Content-Type: text/x-shellscript; charset="us-ascii"
                #!/bin/bash
                set -ex
                /etc/eks/bootstrap.sh {node_group["clusterName"]} \\
                    --b64-cluster-ca {node_group["cluster-ca"]} \\
                    --apiserver-endpoint {node_group["endpoint"]} \\
                    --dns-cluster-ip {node_group["dns"]} \\
                    --container-runtime containerd
                --==MYBOUNDARY==--
                """
            ).strip()
        else:
            logger.error("Invalid parameter value for base AMI")
        encoded_user_data = base64.b64encode(user_data.encode("utf-8")).decode("utf-8")
        if launch_template_id not in processed_launch_templates:
            new_launch_template_response = ec2_client.create_launch_template_version(
                LaunchTemplateId=launch_template_id,
                SourceVersion=str(launch_template_version),
                LaunchTemplateData={
                    "ImageId": image_id,
                    "UserData": encoded_user_data,
                },
            )
            new_launch_template_version_number = str(
                new_launch_template_response["LaunchTemplateVersion"]["VersionNumber"]
            )
            node_group["version"] = new_launch_template_version_number
            processed_launch_templates.add(launch_template_id)
        else:
            launch_template_info = ec2_client.describe_launch_templates(
                LaunchTemplateIds=[launch_template_id]
            )
            latest_version_number = str(
                launch_template_info["LaunchTemplates"][0]["LatestVersionNumber"]
            )
            node_group["version"] = latest_version_number
    return node_groups

@handle_errors
def lambda_handler(event, context):
    """
    Main Lambda function handler.
    Args:
        event: A dictionary containing the event data passed to the Lambda function.
        context: The Lambda function execution context.
    Returns:
        A list of dictionaries containing the updated auto-scaling group information.
    """
    eks = boto3.client("eks")
    clusters = list_clusters(eks)
    filtered_clusters = filter_clusters(eks, clusters, event.get("tags", []))
    node_groups = get_node_groups(eks, filtered_clusters)
    image_id = event["image_id"]
    updated_node_groups = update_launch_templates(node_groups, image_id)
    required_keys = ["launchTemplate", "clusterName", "nodegroupName", "version"]
    response = [
        {key: d[key] for key in required_keys if key in d} for d in updated_node_groups
    ]
    return {"status": "complete", "response": response}
