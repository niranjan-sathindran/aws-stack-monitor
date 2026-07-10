# AWS Stack Monitor

A local Streamlit app for personal AWS stack cost tracking using CloudFormation and Cost Explorer.

This project is built for personal visibility and debugging, not enterprise governance. It gives you a neat local view of:

- CloudFormation stacks
- the AWS services and resources under each stack
- recent monthly cost history
- stack and service totals over time

It is intentionally small, local-first, and read-only.

## What This Repo Is

- A personal utility for understanding AWS spend by CloudFormation stack
- A local app that uses your existing AWS credentials and profiles
- A stack-first cost viewer with resource inventory and monthly rollups

## What This Repo Is Not

- Not an enterprise FinOps platform
- Not a hosted SaaS or centralized governance tool
- Not a guaranteed full accounting of every AWS line item

Some AWS charges can remain unattributed to a specific stack even when the underlying infrastructure was created by CloudFormation.

## How Stack Attribution Works

This app does not require you to add your own custom application tags to resources.

It does, however, rely on AWS-generated CloudFormation cost allocation tags for per-stack billing in Cost Explorer:

- `aws:cloudformation:stack-name`
- `aws:cloudformation:logical-id`
- `aws:cloudformation:stack-id`

If those AWS-generated cost allocation tags are not active, stack cost breakdowns will show as zero or nearly zero even when your account has spend.

Even after activation and backfill, some charges may still remain unattributed. Common examples include:

- account-level or shared networking charges
- Route 53 and registrar charges
- some ELB, VPC, transfer, or other service-level billing lines
- resources not created or tracked by CloudFormation

## AWS Credentials

The app uses your local AWS credentials through the normal boto3 credential chain.

- If you enter a profile name in the sidebar, the app uses that named profile
- If you leave the profile blank, boto3 falls back to its standard resolution chain
- That can include `~/.aws/credentials`, `~/.aws/config`, `AWS_PROFILE`, environment variables, SSO/session credentials, or instance credentials

The app does not store or manage AWS credentials.

## IAM Requirements

Attach the read-only policy in [`iam-policy.json`](./iam-policy.json) to the IAM user or role you want to use locally.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Then open the local URL shown by Streamlit, usually `http://localhost:8501`.

## Cost Allocation Tag Operations

All examples below use your local AWS CLI profile and Cost Explorer in `us-east-1`.

### Check Whether CloudFormation Billing Tags Are Enabled

Active AWS-generated cost allocation tags:

```bash
AWS_PROFILE=default aws ce list-cost-allocation-tags \
  --type AWSGenerated \
  --status Active \
  --region us-east-1
```

Inactive AWS-generated cost allocation tags:

```bash
AWS_PROFILE=default aws ce list-cost-allocation-tags \
  --type AWSGenerated \
  --status Inactive \
  --region us-east-1
```

### Enable CloudFormation Billing Tags

```bash
AWS_PROFILE=default aws ce update-cost-allocation-tags-status \
  --cost-allocation-tags-status '[{"TagKey":"aws:cloudformation:stack-name","Status":"Active"},{"TagKey":"aws:cloudformation:logical-id","Status":"Active"},{"TagKey":"aws:cloudformation:stack-id","Status":"Active"}]' \
  --region us-east-1
```

### Start Backfill

This asks AWS to backfill eligible cost allocation data from the given timestamp.

```bash
AWS_PROFILE=default aws ce start-cost-allocation-tag-backfill \
  --backfill-from 2026-02-01T00:00:00Z \
  --region us-east-1
```

Important:

- AWS can backfill eligible cost allocation data
- this does not guarantee that every historical charge will become attributable to a stack
- some spend may still remain under a blank or unattributed tag value

### Check Backfill Status

```bash
AWS_PROFILE=default aws ce list-cost-allocation-tag-backfill-history \
  --max-results 20 \
  --region us-east-1
```

### Verify Whether Cost Explorer Is Returning Stack Values

Replace the date range as needed:

```bash
AWS_PROFILE=default aws ce get-cost-and-usage \
  --time-period Start=2026-03-01,End=2026-03-19 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=aws:cloudformation:stack-name \
  --region us-east-1
```

You can also list the tag values Cost Explorer currently sees:

```bash
AWS_PROFILE=default aws ce get-tags \
  --time-period Start=2026-03-01,End=2026-03-19 \
  --tag-key aws:cloudformation:stack-name \
  --region us-east-1
```

If the results mostly show a blank value like `aws:cloudformation:stack-name$`, the app will still show most stacks at zero because AWS has not attributed that spend to stack names yet.

## Troubleshooting

### Stack costs are still zero after enabling the AWS-generated tags

This is a real scenario we hit while testing on March 18, 2026:

- `aws:cloudformation:stack-name`, `aws:cloudformation:logical-id`, and `aws:cloudformation:stack-id` were all `Active`
- a backfill from `2026-02-01T00:00:00Z` had already `SUCCEEDED`
- Cost Explorer still returned almost all current-month spend under the blank tag value
- only tiny amounts were attributed to a small number of stack-tagged frontend resources

In other words, enabling the tags and running backfill helped the account become eligible for stack attribution, but it still did not make every billed service map cleanly to a CloudFormation stack.

If this happens:

1. Wait and check again later. AWS attribution can continue to settle after activation/backfill.
2. Inspect the raw Cost Explorer grouped output with the commands above.
3. Expect some charges to remain unattributed even long term.
4. Use the app's `(untagged)` and service-level views to understand where the unattributed spend is landing.

### Why a real stack can still show `$0.00`

Possible reasons:

- AWS has not yet attributed eligible spend to `aws:cloudformation:stack-name`
- the stack's resources are free-tier or currently negligible
- the charged service line is not stack-attributable in Cost Explorer
- the charge belongs to a shared or account-level resource

### Why the app says "no user-defined tags required" but still talks about tags

Because the distinction matters:

- you do not need to invent and manage your own custom cost tags for this app
- you do still need AWS-generated CloudFormation cost allocation tags to be active for per-stack billing

## Open Source Notes

This repo is intended to be open sourced as a small personal utility. Keep expectations light:

- local-first
- read-only
- no SLA or support guarantee
- best-effort stack attribution based on what AWS Cost Explorer exposes

## License

This project uses the Apache License 2.0. See [`LICENSE`](./LICENSE).
