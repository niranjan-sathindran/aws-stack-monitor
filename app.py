from collections import defaultdict
from datetime import date, timedelta

import boto3
import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta

STACK_TAG_KEY = "aws:cloudformation:stack-name"
UNTAGGED_LABEL = "(untagged)"
MONTH_COUNT = 6
ACTIVE_STACK_STATUSES = [
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
    "ROLLBACK_COMPLETE",
    "IMPORT_COMPLETE",
    "IMPORT_ROLLBACK_COMPLETE",
]
RESOURCE_FAMILY_OVERRIDES = {
    "ApiGateway": "API Gateway",
    "ApplicationAutoScaling": "Application Auto Scaling",
    "AutoScaling": "Auto Scaling",
    "CertificateManager": "Certificate Manager",
    "CloudFormation": "CloudFormation",
    "CloudFront": "CloudFront",
    "CloudWatch": "CloudWatch",
    "CodeBuild": "CodeBuild",
    "CodePipeline": "CodePipeline",
    "Cognito": "Cognito",
    "DynamoDB": "DynamoDB",
    "EC2": "EC2",
    "ECR": "ECR",
    "ECS": "ECS",
    "EFS": "EFS",
    "EKS": "EKS",
    "ElasticLoadBalancing": "Elastic Load Balancing",
    "ElasticLoadBalancingV2": "Elastic Load Balancing",
    "Events": "EventBridge",
    "IAM": "IAM",
    "KMS": "KMS",
    "Lambda": "Lambda",
    "Logs": "CloudWatch Logs",
    "OpenSearchService": "OpenSearch",
    "RDS": "RDS",
    "Route53": "Route 53",
    "S3": "S3",
    "SNS": "SNS",
    "SQS": "SQS",
    "SSM": "Systems Manager",
    "Scheduler": "EventBridge Scheduler",
    "SecretsManager": "Secrets Manager",
    "StepFunctions": "Step Functions",
}
RESOURCE_TO_BILLING_SERVICE = {
    "API Gateway": "Amazon API Gateway",
    "CloudFormation": "AWS CloudFormation",
    "CloudFront": "Amazon CloudFront",
    "CloudWatch": "AmazonCloudWatch",
    "CloudWatch Logs": "AmazonCloudWatch",
    "CodeBuild": "AWS CodeBuild",
    "CodePipeline": "AWS CodePipeline",
    "Cognito": "Amazon Cognito",
    "DynamoDB": "Amazon DynamoDB",
    "EC2": "Amazon Elastic Compute Cloud - Compute",
    "ECR": "Amazon EC2 Container Registry (ECR)",
    "ECS": "Amazon Elastic Container Service",
    "EFS": "Amazon Elastic File System",
    "EKS": "Amazon Elastic Kubernetes Service",
    "Elastic Load Balancing": "Amazon Elastic Load Balancing",
    "EventBridge": "Amazon EventBridge",
    "EventBridge Scheduler": "Amazon EventBridge",
    "KMS": "AWS Key Management Service",
    "Lambda": "AWS Lambda",
    "OpenSearch": "Amazon OpenSearch Service",
    "RDS": "Amazon Relational Database Service",
    "Route 53": "Amazon Route 53",
    "S3": "Amazon Simple Storage Service",
    "SNS": "Amazon Simple Notification Service",
    "SQS": "Amazon Simple Queue Service",
    "Secrets Manager": "AWS Secrets Manager",
    "Step Functions": "AWS Step Functions",
    "Systems Manager": "AWS Systems Manager",
}


def make_client(service: str, profile: str, region: str):
    session_kwargs = {}
    if profile.strip():
        session_kwargs["profile_name"] = profile.strip()
    session = boto3.Session(**session_kwargs)
    client_region = "us-east-1" if service == "ce" else region
    return session.client(service, region_name=client_region)


def get_month_windows(today: date, months: int):
    windows = []
    current_month_start = today.replace(day=1)

    for offset in reversed(range(months)):
        month_start = current_month_start - relativedelta(months=offset)
        month_end = month_start + relativedelta(months=1)
        if offset == 0:
            month_end = min(today + timedelta(days=1), month_end)

        label = month_start.strftime("%b %Y")
        if offset == 0:
            label = f"{label} (MTD)"

        windows.append(
            {
                "key": month_start.strftime("%Y-%m"),
                "label": label,
                "short_label": month_start.strftime("%b %Y"),
                "start": month_start.strftime("%Y-%m-%d"),
                "end": month_end.strftime("%Y-%m-%d"),
            }
        )

    return windows


def parse_stack_tag_value(raw_key: str):
    if not raw_key:
        return UNTAGGED_LABEL

    if "$" in raw_key:
        raw_key = raw_key.split("$", 1)[1]

    raw_key = raw_key.strip()
    if not raw_key or raw_key.lower().startswith("no tagkey"):
        return UNTAGGED_LABEL
    return raw_key


def get_resource_family(resource_type: str):
    parts = resource_type.split("::")
    if len(parts) >= 2 and parts[0] == "AWS":
        return RESOURCE_FAMILY_OVERRIDES.get(parts[1], parts[1])
    return resource_type


def format_currency(value: float):
    return f"${value:,.2f}"


def format_currency_plain(value: float):
    return f"USD {value:,.2f}"


def format_delta(value: float):
    return f"{value:+,.2f}"


def get_month_key_from_period(period):
    return period["TimePeriod"]["Start"][:7]


def fetch_cost_pages(profile: str, region: str, start: str, end: str, group_by=None):
    ce = make_client("ce", profile, region)
    params = {
        "TimePeriod": {"Start": start, "End": end},
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
    }
    if group_by:
        params["GroupBy"] = group_by

    pages = []
    next_page_token = None

    while True:
        request = dict(params)
        if next_page_token:
            request["NextPageToken"] = next_page_token
        response = ce.get_cost_and_usage(**request)
        pages.append(response)
        next_page_token = response.get("NextPageToken")
        if not next_page_token:
            break

    return pages


@st.cache_data(ttl=900, show_spinner=False)
def get_total_costs_by_month(profile: str, region: str, start: str, end: str):
    totals = defaultdict(float)
    for page in fetch_cost_pages(profile, region, start, end):
        for period in page["ResultsByTime"]:
            month_key = get_month_key_from_period(period)
            totals[month_key] += float(period["Total"]["UnblendedCost"]["Amount"])
    return {month_key: round(value, 2) for month_key, value in totals.items()}


@st.cache_data(ttl=900, show_spinner=False)
def get_costs_by_stack_by_month(profile: str, region: str, start: str, end: str):
    nested_totals = defaultdict(lambda: defaultdict(float))
    pages = fetch_cost_pages(
        profile,
        region,
        start,
        end,
        group_by=[{"Type": "TAG", "Key": STACK_TAG_KEY}],
    )

    for page in pages:
        for period in page["ResultsByTime"]:
            month_key = get_month_key_from_period(period)
            for group in period["Groups"]:
                stack_name = parse_stack_tag_value(group["Keys"][0])
                nested_totals[stack_name][month_key] += float(
                    group["Metrics"]["UnblendedCost"]["Amount"]
                )

    return {
        stack_name: {
            month_key: round(cost, 2)
            for month_key, cost in month_costs.items()
            if round(cost, 2) > 0
        }
        for stack_name, month_costs in nested_totals.items()
    }


@st.cache_data(ttl=900, show_spinner=False)
def get_costs_by_stack_and_service_by_month(profile: str, region: str, start: str, end: str):
    nested_totals = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    pages = fetch_cost_pages(
        profile,
        region,
        start,
        end,
        group_by=[
            {"Type": "TAG", "Key": STACK_TAG_KEY},
            {"Type": "DIMENSION", "Key": "SERVICE"},
        ],
    )

    for page in pages:
        for period in page["ResultsByTime"]:
            month_key = get_month_key_from_period(period)
            for group in period["Groups"]:
                stack_name = parse_stack_tag_value(group["Keys"][0])
                billing_service = group["Keys"][1]
                nested_totals[stack_name][billing_service][month_key] += float(
                    group["Metrics"]["UnblendedCost"]["Amount"]
                )

    cleaned = {}
    for stack_name, services in nested_totals.items():
        cleaned[stack_name] = {}
        for billing_service, month_costs in services.items():
            rounded = {
                month_key: round(cost, 2)
                for month_key, cost in month_costs.items()
                if round(cost, 2) > 0
            }
            if rounded:
                cleaned[stack_name][billing_service] = rounded
    return cleaned


def get_stacks(profile: str, region: str):
    cf = make_client("cloudformation", profile, region)
    paginator = cf.get_paginator("list_stacks")
    stacks = []
    for page in paginator.paginate(StackStatusFilter=ACTIVE_STACK_STATUSES):
        stacks.extend(page["StackSummaries"])
    return sorted(stacks, key=lambda stack: stack["StackName"].lower())


def get_stack_resources(profile: str, region: str, stack_name: str):
    cf = make_client("cloudformation", profile, region)
    paginator = cf.get_paginator("list_stack_resources")
    resources = []
    for page in paginator.paginate(StackName=stack_name):
        resources.extend(page["StackResourceSummaries"])
    return resources


@st.cache_data(ttl=900, show_spinner=False)
def load_stack_inventory(profile: str, region: str):
    stack_inventory = {}
    for stack in get_stacks(profile, region):
        stack_name = stack["StackName"]
        resources = get_stack_resources(profile, region, stack_name)

        resources_rows = []
        service_summary = defaultdict(lambda: {"Resource Count": 0, "Resource Types": set()})
        for resource in resources:
            service_name = get_resource_family(resource["ResourceType"])
            resources_rows.append(
                {
                    "Service": service_name,
                    "Logical ID": resource["LogicalResourceId"],
                    "Type": resource["ResourceType"],
                    "Status": resource["ResourceStatus"],
                    "Physical ID": resource.get("PhysicalResourceId", ""),
                }
            )
            service_summary[service_name]["Resource Count"] += 1
            service_summary[service_name]["Resource Types"].add(resource["ResourceType"])

        service_rows = []
        for service_name, summary in sorted(service_summary.items()):
            service_rows.append(
                {
                    "Service": service_name,
                    "Resource Count": summary["Resource Count"],
                    "Resource Types": ", ".join(sorted(summary["Resource Types"])),
                }
            )

        stack_inventory[stack_name] = {
            "stack_name": stack_name,
            "resource_count": len(resources_rows),
            "resource_rows": resources_rows,
            "service_rows": service_rows,
            "service_count": len(service_rows),
        }

    return stack_inventory


def build_stack_rows(stack_inventory, stack_costs_by_month, month_windows):
    all_stack_names = sorted(
        set(stack_inventory.keys()) | set(stack_costs_by_month.keys()),
        key=str.lower,
    )

    rows = []
    for stack_name in all_stack_names:
        inventory = stack_inventory.get(
            stack_name,
            {"resource_count": 0, "service_count": 0},
        )
        row = {
            "Stack": stack_name,
            "Services": inventory["service_count"],
            "Resources": inventory["resource_count"],
            "In CloudFormation": "Yes" if stack_name in stack_inventory else "No",
        }

        total = 0.0
        monthly_costs = stack_costs_by_month.get(stack_name, {})
        for window in month_windows:
            value = monthly_costs.get(window["key"], 0.0)
            row[window["label"]] = value
            total += value

        row["Total ($)"] = round(total, 2)
        rows.append(row)

    return rows


def build_service_cost_rows(inventory_services, service_costs_by_month, month_windows):
    inventory_lookup = {row["Service"]: row for row in inventory_services}
    billing_service_lookup = {
        service_name: RESOURCE_TO_BILLING_SERVICE.get(service_name)
        for service_name in inventory_lookup
    }

    rows = []
    used_billing_services = set()

    for service_name, inventory_row in sorted(inventory_lookup.items()):
        billing_service = billing_service_lookup.get(service_name)
        row = {
            "Service": service_name,
            "Billing Service": billing_service or "",
            "Resources": inventory_row["Resource Count"],
        }

        total = 0.0
        monthly_costs = service_costs_by_month.get(billing_service, {}) if billing_service else {}
        if billing_service:
            used_billing_services.add(billing_service)

        for window in month_windows:
            value = monthly_costs.get(window["key"], 0.0)
            row[window["label"]] = value
            total += value

        row["Total ($)"] = round(total, 2)
        rows.append(row)

    extra_billing_services = sorted(set(service_costs_by_month) - used_billing_services)
    for billing_service in extra_billing_services:
        row = {
            "Service": "Billing-only",
            "Billing Service": billing_service,
            "Resources": 0,
        }
        total = 0.0
        monthly_costs = service_costs_by_month[billing_service]
        for window in month_windows:
            value = monthly_costs.get(window["key"], 0.0)
            row[window["label"]] = value
            total += value

        row["Total ($)"] = round(total, 2)
        rows.append(row)

    return rows


def add_total_row(df: pd.DataFrame, label_column: str, label_value: str, cost_columns, sum_columns):
    if df.empty:
        return df

    summary_row = {column: "" for column in df.columns}
    summary_row[label_column] = label_value

    for column in cost_columns:
        summary_row[column] = round(pd.to_numeric(df[column], errors="coerce").fillna(0).sum(), 2)

    for column in sum_columns:
        summary_row[column] = int(pd.to_numeric(df[column], errors="coerce").fillna(0).sum())

    return pd.concat([df, pd.DataFrame([summary_row])], ignore_index=True)


def build_cost_column_config(month_labels):
    config = {"Total ($)": st.column_config.NumberColumn(format="$%.2f")}
    for label in month_labels:
        config[label] = st.column_config.NumberColumn(format="$%.2f")
    return config


st.set_page_config(page_title="AWS Stack Monitor", layout="wide")
st.title("AWS Stack Cost Monitor")
st.caption("Stack-first monthly cost tracking for CloudFormation-managed AWS infrastructure.")

with st.sidebar:
    st.header("AWS Config")
    profile = st.text_input("AWS profile", value="")
    region = st.text_input("Region", value="us-east-1")
    show_zero_cost_stacks = st.checkbox("Show $0 stacks", value=True)
    refresh_requested = st.button("Refresh data", use_container_width=True)

if refresh_requested:
    st.cache_data.clear()

month_windows = get_month_windows(date.today(), MONTH_COUNT)
display_month_windows = list(reversed(month_windows))
month_labels = [window["label"] for window in display_month_windows]
latest_window = month_windows[-1]
previous_window = month_windows[-2]
range_start = month_windows[0]["start"]
range_end = month_windows[-1]["end"]

try:
    with st.spinner("Loading CloudFormation inventory and Cost Explorer data..."):
        stack_inventory = load_stack_inventory(profile, region)
        total_costs_by_month = get_total_costs_by_month(profile, region, range_start, range_end)
        stack_costs_by_month = get_costs_by_stack_by_month(profile, region, range_start, range_end)
        service_costs_by_month = get_costs_by_stack_and_service_by_month(
            profile, region, range_start, range_end
        )
except Exception as exc:
    st.error(f"AWS connection failed: {exc}")
    st.stop()

stack_rows = build_stack_rows(stack_inventory, stack_costs_by_month, display_month_windows)
if not show_zero_cost_stacks:
    stack_rows = [row for row in stack_rows if row["Total ($)"] > 0]

stack_df = pd.DataFrame(stack_rows)
if not stack_df.empty:
    stack_df = stack_df.sort_values(
        by=[latest_window["label"], "Total ($)", "Stack"],
        ascending=[False, False, True],
    )

untagged_monthly = stack_costs_by_month.get(UNTAGGED_LABEL, {})
latest_total = total_costs_by_month.get(latest_window["key"], 0.0)
previous_total = total_costs_by_month.get(previous_window["key"], 0.0)
visible_total = round(sum(total_costs_by_month.get(window["key"], 0.0) for window in month_windows), 2)
latest_untagged = untagged_monthly.get(latest_window["key"], 0.0)
tagged_latest_total = round(
    sum(
        month_costs.get(latest_window["key"], 0.0)
        for stack_name, month_costs in stack_costs_by_month.items()
        if stack_name != UNTAGGED_LABEL
    ),
    2,
)

st.subheader("Monthly Overview")
top_metrics = st.columns(4)
top_metrics[0].metric(latest_window["label"], format_currency(latest_total))
top_metrics[1].metric(previous_window["label"], format_currency(previous_total))
top_metrics[2].metric(f"{MONTH_COUNT}-month total", format_currency(visible_total))
top_metrics[3].metric("Stacks discovered", str(len(stack_inventory)))
st.caption(f"Showing {month_labels[0]} through {month_labels[-1]}.")

untagged_breakdown = [
    f"{window['label']}: {format_currency_plain(untagged_monthly.get(window['key'], 0.0))}"
    for window in month_windows
    if untagged_monthly.get(window["key"], 0.0) > 0
]
if untagged_breakdown:
    st.warning(
        "Some spend is still unassigned to a CloudFormation stack. "
        + " | ".join(untagged_breakdown)
        + ". That usually means the resource is missing the stack cost allocation tag or AWS reported the charge at an account-level service line item."
    )

if latest_total > 0 and latest_untagged >= latest_total - 0.01 and tagged_latest_total == 0:
    st.error(
        "Cost Explorer is still not returning any non-empty values for "
        f"`{STACK_TAG_KEY}` in {latest_window['label']}. "
        "That is why every real CloudFormation stack still shows $0 right now."
    )

    unattributed_service_df = pd.DataFrame(
        build_service_cost_rows(
            [],
            service_costs_by_month.get(UNTAGGED_LABEL, {}),
            display_month_windows,
        )
    )
    if not unattributed_service_df.empty:
        unattributed_service_df = unattributed_service_df.sort_values(
            by=[latest_window["label"], "Total ($)", "Billing Service"],
            ascending=[False, False, True],
        )
        unattributed_service_df = add_total_row(
            unattributed_service_df,
            "Service",
            "TOTAL",
            month_labels + ["Total ($)"],
            ["Resources"],
        )

        st.markdown("**Unattributed spend by billing service**")
        st.dataframe(
            unattributed_service_df,
            width="stretch",
            hide_index=True,
            column_config=build_cost_column_config(month_labels),
        )

st.divider()

st.subheader("Stacks")
stack_filter = st.text_input("Filter stack names", value="")

if not stack_df.empty and stack_filter.strip():
    stack_df = stack_df[stack_df["Stack"].str.contains(stack_filter, case=False, na=False)]

if stack_df.empty:
    st.info(
        "No stack cost data was found. Make sure the "
        f"`{STACK_TAG_KEY}` cost allocation tag is active in AWS Billing."
    )
    st.stop()

stack_display_df = add_total_row(
    stack_df,
    "Stack",
    "TOTAL",
    month_labels + ["Total ($)"],
    ["Services", "Resources"],
)

stack_column_config = build_cost_column_config(month_labels)
stack_column_config["Services"] = st.column_config.NumberColumn(format="%d")
stack_column_config["Resources"] = st.column_config.NumberColumn(format="%d")

st.dataframe(
    stack_display_df,
    width="stretch",
    hide_index=True,
    column_config=stack_column_config,
)

st.divider()
st.subheader("Stack Details")

for row in stack_df.to_dict("records"):
    stack_name = row["Stack"]
    inventory = stack_inventory.get(
        stack_name,
        {
            "resource_count": 0,
            "resource_rows": [],
            "service_rows": [],
            "service_count": 0,
        },
    )
    stack_monthly_costs = stack_costs_by_month.get(stack_name, {})
    latest_stack_cost = stack_monthly_costs.get(latest_window["key"], 0.0)
    previous_stack_cost = stack_monthly_costs.get(previous_window["key"], 0.0)

    title = (
        f"{stack_name} | "
        f"{format_currency_plain(latest_stack_cost)} in {latest_window['short_label']} | "
        f"{format_currency_plain(row['Total ($)'])} across {MONTH_COUNT} months"
    )
    with st.expander(title, expanded=False):
        detail_metrics = st.columns(4)
        detail_metrics[0].metric(latest_window["label"], format_currency(latest_stack_cost))
        detail_metrics[1].metric(previous_window["label"], format_currency(previous_stack_cost))
        detail_metrics[2].metric("6-month total", format_currency(row["Total ($)"]))
        detail_metrics[3].metric(
            "Resources / services",
            f"{inventory['resource_count']} / {inventory['service_count']}",
        )

        service_df = pd.DataFrame(
            build_service_cost_rows(
                inventory["service_rows"],
                service_costs_by_month.get(stack_name, {}),
                display_month_windows,
            )
        )
        if not service_df.empty:
            service_df = service_df.sort_values(
                by=[latest_window["label"], "Total ($)", "Service"],
                ascending=[False, False, True],
            )
            service_df = add_total_row(
                service_df,
                "Service",
                "TOTAL",
                month_labels + ["Total ($)"],
                ["Resources"],
            )

            service_column_config = build_cost_column_config(month_labels)
            service_column_config["Resources"] = st.column_config.NumberColumn(format="%d")

            st.markdown("**Service Summary**")
            st.dataframe(
                service_df,
                width="stretch",
                hide_index=True,
                column_config=service_column_config,
            )
        else:
            st.info("No service-level cost or inventory data is available for this stack.")

        resources_df = pd.DataFrame(inventory["resource_rows"])
        if not resources_df.empty:
            st.markdown("**Resources**")
            st.dataframe(
                resources_df.sort_values(by=["Service", "Logical ID"]),
                width="stretch",
                hide_index=True,
            )
        elif stack_name == UNTAGGED_LABEL:
            st.info(
                "Untagged spend is not tied to a CloudFormation stack, so there is no resource inventory to show."
            )
        else:
            st.info("CloudFormation has no active resources for this stack.")
