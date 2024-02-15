# EKS-Optimized AMI Hardening Pipeline

## Description

This repository contains a CloudFormation template that automates the creation of an EC2 Image Builder pipeline. The pipeline applies CIS Amazon Linux 2 benchmarks to an Amazon EKS-Optimized AMI using an Ansible playbook. The resulting hardened AMI is intended for use in updating Amazon EKS cluster node groups, enhancing security and compliance.

## Features

- Automated hardening of Amazon EKS-Optimized AMI against CIS Amazon Linux 2 benchmarks.
- Customizable Ansible playbook arguments for tailored AMI hardening.
- Integration with Amazon Inspector for enhanced AMI scanning.
- Automated updates to EKS cluster node groups with hardened AMIs.

## Prerequisites

- An AWS account with permissions to create the necessary resources.
- An existing Amazon EKS cluster or the intention to create one.
- AWS CLI installed
- Amazon Inspector for EC2 enabled in your AWS account

## Quick Start

1. Clone this repository to your local machine.
2. Navigate to the AWS CloudFormation console.
3. Choose "Create stack" and upload the provided [CloudFormation template](CloudFormation/AMI-Pipeline-Auto-Replace.yml).
4. Fill in the parameters as per your requirements. See the Parameters section below for details.
5. Follow the on-screen instructions to create the stack.

## Parameters

- `AnsiblePlaybookArguments`: Custom arguments for the `ansible-playbook` command.
- `LatestEKSOptimizedAMI`: The AWS Systems Manager Parameter Store parameter for the AMI ID.
- `InstanceType`: EC2 instance type for Image Builder Infrastructure.
- `ComponentName`, `RecipeName`, `InfrastructureConfigurationName`, `DistributionConfigurationName`, `ImagePipelineName`: Naming conventions for the Image Builder components.
- `EnableImageScanning`: Toggle for Amazon Inspector AMI scanning.
- `ClusterTags`: JSON string of key-value pairs to filter EKS clusters.
- `CloudFormationUpdaterEventBridgeRuleState`: State of the EventBridge rule for weekend checks on new base images.

## Architecture

![Solution Architecture](images/architecture_diagram.png)

This diagram illustrates the workflow of the EC2 Image Builder pipeline, from fetching the latest EKS-Optimized AMI to applying CIS benchmarks and distributing the hardened AMI.

## Contributing

We welcome contributions and suggestions! Please fork the repository and submit pull requests with your improvements. Check out the [CONTRIBUTING.md](CONTRIBUTING.md) file for guidelines on contributing.

## License

This project is licensed under the [MIT License](LICENSE).
