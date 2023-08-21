# Deploying onto Amazon Elastic Kubernetes Service (Amazon EKS)

Below is described steps for deployment of Mist onto [Amazon Elastic Kubernetes Service](https://aws.amazon.com/pm/eks/).
Although the steps are unique to EKS, the Kubernetes files may be applied to other platforms, such as Google Kubernetes Engine (GKE).
To do so, simply modify the deployment commands to suit the target platform.

This Kubernetes deployment takes advantage of autoscaling burst node pools, so that the expensive GPU node is only active while in use.
Note, the Docker container will need to initialize on a fresh node, which has a lead time of 5-6 minutes.
After the pod is initialized, it acts just like any other deployment. Once the pod job has been completed or deleted, the GPU node will
autoscale to 0.

## Prerequisites

Ensure you have created an Amazon Web Services account and configure the account default region.
You will also need to request a quota increase for G4 GPU instances.
The Mist image and runtime packages exceed 16GB of memory, which corresponds to the [g4ax.2xlarge](https://instances.vantage.sh/aws/ec2/g4ad.2xlarge) machine type.
This machine type requires 8vCPU, so in your [service request](https://blog.deploif.ai/posts/aws_quota), you must request at least 8vCPU.

### Notes on Amazon Web Services

If it is your first time registering, you will have a certain amount of free credits.
Keep an eye on the [Amazon Billing](https://aws.amazon.com/aws-cost-management/aws-billing/) webpage and delete resources after you are done using them.
This will ensure you accrue minimal costs.

You will need to install the [Amazon CLI](https://aws.amazon.com/cli/) to complete some of the steps below.

### Install `eksctl` and Helm

`eksctl` is used to create and manage Kubernetes instances on the AWS platform. Helm is used to help manage Kubernetes applications.
In our case, we will use it to install the [AWS loadbalancer controller addon](https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html).

#### Resources
* [Installing or updating `eksctl`](https://docs.aws.amazon.com/eks/latest/userguide/eksctl.html)
* [Install Helm](https://helm.sh/docs/intro/install/)
* [Install or update the latest version of the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)

## Steps

### Build the Docker container for the control node

Create and then login to your [Amazon Elastic Container Registry](https://aws.amazon.com/ecr/).

```
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <AWS ACCOUNT ID>.dkr.ecr.<REGION>.amazonaws.com/<ECR NAME>
```

**Note:** Below we will refer to the ECR connection URL as "\<ECR REPO\>".

Tag and push the burst pool image which will be started at runtime. More instructions to follow on what image to use.
For now, we are testing whether the [documented cloud image](https://mist-documentation.readthedocs.io/en/latest/content/cloud.html) may be used as is or will need to be modified.

```
docker tag public.ecr.aws/f9c5c8j0/mist-with-model:latest <ECR REPO>:<AWS BURST IMAGE>
docker push <ECR REPO>:<AWS BURST IMAGE>
```

Next build, tag, and push the control image.

**Important:** Before building, ensure you open the `app.js` file and replace "\<MIST RUNTIME IMAGE\>" with the ECR image and tag of where you pushed the burst pool image from above.

```
docker build -t <CONTROL IMAGE>:<CONTROL TAG> .
docker tag <CONTROL IMAGE>:<CONTROL TAG> <ECR REPO>:<AWS CONTROL IMAGE>
docker push <ECR REPO>:<AWS CONTROL IMAGE>
```

#### Resources
* [Pushing a Docker Image to ECR](https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html)

### Create the Kubernetes cluster

The `cluster.yaml` file contains the cluster definition. **Note:** Update the "\<REGION\>" before running the create command.

```
eksctl create cluster -f cluster.yaml
```

#### Resources
* [Creating an Amazon EKS cluster](https://docs.aws.amazon.com/eks/latest/userguide/create-cluster.html)
* [Deploying Kubernetes cluster with YAML on AWS EKS](https://awstip.com/deploying-kubernetes-with-yaml-on-aws-eks-c22ade1bf3ca)

### Configure the application loadbalancer

A loadbalancer is used to expose the cluster to the public internet. In this section the loadbalancer is configured.

The following command will apply the [NVIDIA GPU daemonset](https://catalog.ngc.nvidia.com/orgs/nvidia/containers/k8s-device-plugin).
This daemonset will expose NVIDIA resources to the kubelet.

```
kubectl apply -f https://raw.githubusercontent.com/kubernetes/kubernetes/master/cluster/addons/device-plugins/nvidia-gpu/daemonset.yaml
```

Configure the AWS Loadbalancer Controller add-on with appopriate IAM permissions. Then install the loadbalancer.

```
eksctl utils associate-iam-oidc-provider --cluster mist --approve
curl -O https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.5.4/docs/install/iam_policy.json
aws iam create-policy --policy-name AWSLoadBalancerControllerIAMPolicy --policy-document file://iam_policy.json
eksctl create iamserviceaccount --cluster=mist --namespace=kube-system --name=aws-load-balancer-controller --attach-policy-arn=arn:aws:iam::<AWS ACCOUNT ID>:policy/AWSLoadBalancerControllerIAMPolicy --override-existing-serviceaccounts --approve
```

Delete the old ALB loadbalancer and add the EKS repo for Helm to reference.

```
helm delete aws-alb-ingress-controller -n kube-system
helm repo add eks https://aws.github.io/eks-charts
helm repo update eks
```

**Important:** You will replace the VPC in the `helm install` command below with the VPC for your cluster.
The command below will print information you may pull the VPC from.

```
aws eks describe-cluster --name mist
```

Finally, install the loadbalancer via Helm. **Note:** Make sure to replace the "\<REGION\>" and "\<VPC ID\>" before running the command.

```
helm install aws-load-balancer-controller eks/aws-load-balancer-controller --set clusterName=mist --set serviceAccount.create=false --set region=<REGION> --set vpcId=<VPC ID> --set serviceAccount.name=aws-load-balancer-controller -n kube-system
```

#### Resources
* [Installing the AWS Load Balancer Controller add-on](https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html)
* [Install Helm](https://helm.sh/docs/intro/install/)

### Create the job service roles and role bindings

The Mist control container will be able to create jobs.
If no nodes are available in the burst pool, the cluster's autoscaler will kick in and create a new node for the pod to launch onto.
Applying the following roles and role bindings will allow for the control container the permissions to do this at runtime.

```
kubectl create serviceaccount job-robot
kubectl apply -f role.yaml
kubectl apply -f rolebinding.yaml
```

#### Resources
* [Accessing Kubernetes API from a Pod (RBAC)](https://blog.meain.io/2019/accessing-kubernetes-api-from-pod/)

### Deploy the control pod, service, and ingress

It's now time to deploy the control container. Use the below command to deploy the control pod, service, and ingress necessary to support the main server.

**Note:** Replace the "\<IMAGE\>" in the `deployment.yaml` file to point to the location of the ECR location you pushed the control image to.
The service name, "job-robot", must match the service defined in the `role.yaml` and `rolebinding.yaml` files.

```
kubectl apply -f deployment.yaml
```

### Configure the autoscaler

Create an ASG Autodiscovery policy and deployment to allow the autoscaler to work in the cluster.

```
aws iam create-policy --policy-name ASGAutodiscoveryIAMPolicy --policy-document file://cluster-autoscaler-policies.json
eksctl create iamserviceaccount --cluster=mist --namespace=kube-system --name=cluster-autoscaler --attach-policy-arn=arn:aws:iam::<AWS ACCOUNT ID>:policy/ASGAutodiscoveryIAMPolicy --override-existing-serviceaccounts --approve
kubectl apply -f cluster-autoscaler-autodiscover.yaml
```

#### Resources
* [Cluster Autoscaler on AWS](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/cloudprovider/aws/README.md)

### Connect to the deployment (ingress)

Use the following command to find the endpoint for the cluster.
**Note:** It takes a few minutes to initialize the ingress components, so if the endpoint is not responsive right away wait a minute and then try again.

```
kubectl get ingress -A
```

#### Resources
* [Application load balancing on Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/alb-ingress.html)

## Debugging and Cleanup

### Debug autoscaling

A lot can go wrong in the autoscaler. The commands below will help you debug and health-check the autoscaler deployment.

```
aws autoscaling describe-auto-scaling-groups
kubectl describe deployment cluster-autoscaler -n kube-system
```

### Removing components

Run the following commands consequtively in order to completely dismantle the cluster and all its components.

**Note:** Sometimes the [stack](https://aws.amazon.com/cloudformation/) is not deleted correctly. Give it some time, and then try the deletion command again,
or navigate to the stack in the AWS console and delete it from there.

```
kubectl delete job mist-reserved
kubectl delete -f deployment-service-ingress.yaml
eksctl delete cluster -f cluster.yaml
```
