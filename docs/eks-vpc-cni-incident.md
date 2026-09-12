# EKS VPC CNI incident postmortem

## Summary

During Probes' temporary EKS learning deployment, node group provisioning stalled even
though two EC2 instances were running and had joined the cluster. Both nodes were
`NotReady`. Investigation found that the `aws-node` VPC CNI DaemonSet was absent from
`kube-system`, despite the cluster-creation log reporting successful installation.
Reinstalling the `vpc-cni` addon through the AWS CLI was followed by both nodes becoming
`Ready` within approximately one minute.

This account was moved from the [technical case study](CASE-STUDY.md). It records the
original lab observations; the cluster was subsequently deleted to control cloud costs.
Render remains the live deployment described in the [README](../README.md).

## Impact and detection

The incident blocked progress on the EKS learning deployment: node group provisioning
stalled and the joined nodes were not ready for the application deployment. The account
does not establish an outage of the live Render service.

Detection began with a node group provisioning timeout from `eksctl`. The initial error
did not identify the underlying problem, so investigation moved through the infrastructure
layers to inspect their actual state.

## Investigation sequence

Exact incident timestamps and total duration were not recorded in the source narrative.
The following is the recorded order of events, not a reconstructed timestamped timeline.

| Step | Observation or action | What it established |
|---|---|---|
| 1 | Inspected raw CloudFormation stack events after the initial `eksctl` failure | The chosen instance type was not Free Tier eligible for that AWS account, explaining the first attempt's rollback |
| 2 | Switched instance types and retried provisioning | The second attempt stalled without a CloudFormation failure, requiring a separate investigation |
| 3 | Checked Auto Scaling Group activity | The EC2 instances had launched |
| 4 | Checked both EC2 instance IDs | Both instances were `running`, ruling out an instance-launch failure |
| 5 | Ran `kubectl get nodes` | Both nodes had joined but were `NotReady` |
| 6 | Inspected `kube-system` | The `aws-node` DaemonSet was absent despite the earlier installation-success log |
| 7 | Reinstalled the `vpc-cni` addon through the AWS CLI | Both nodes became `Ready` within approximately one minute |

## Cause and contributing factors

The recorded operational cause of the second stall was the missing VPC CNI DaemonSet.
The cause of its absence was not established. The available account does not support
attributing it to a specific tool bug, IAM policy, manual deletion, or addon version.

The first attempt's instance-type eligibility failure was a separate provisioning problem.
Treating the later stall as the same failure would have missed the observed Kubernetes state.
The earlier installation-success message also did not establish that the addon was still
present and healthy when the nodes were inspected.

## Resolution and verification

The addon was reinstalled using the AWS CLI. The exact command, addon version, cluster name,
and region were not preserved in the source narrative, so this postmortem does not invent
a command transcript. Both nodes becoming `Ready` was the recorded recovery check.

The broader lab account subsequently records successful access to Probes' `/api/status`
through the internet-facing load balancer. After testing, the temporary cluster was deleted
and the original case study records AWS CLI checks for remaining resources.

## Lessons and follow-up

- Inspect CloudFormation, Auto Scaling, EC2, and Kubernetes state in sequence to locate the
  failing layer instead of relying only on the provisioning tool's final error.
- Verify addon presence and node readiness after provisioning, even when installation logs
  report success. The [runbook](../RUNBOOK.md#kubernetes-lab-debugging) includes read-only
  checks for nodes and the `aws-node` DaemonSet.
- For any future lab incident, preserve timestamps, node conditions, addon state, commands,
  and recovery output with credentials and account-specific identifiers redacted.

## Evidence status

The existing case-study narrative is the source for this postmortem. Raw CloudFormation
events, command transcripts, and incident screenshots are not attached to this document.
The approximate one-minute recovery is reported from that narrative, not independently
remeasured. Adding authentic node-readiness and VPC CNI evidence remains a documentation task;
no cluster was created or changed while preparing this postmortem.
