import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    getInfraEmailTemplate,
    getInfraEmailSubject,
    summarizeInfraError,
    ALL_INFRA_EVENTS,
} from './infra-email.template';
import { getAuthEmailTemplate } from './auth-email.template';
import { getForgotPasswordTemplate } from './forgot-password.template';

// A realistic raw terraform/boto3 failure — every token here is sensitive and must never reach an inbox.
const RAW_LEAKY_ERROR = `Error: error configuring Terraform AWS Provider: operation error STS: GetCallerIdentity,
https response error StatusCode: 403, api error AccessDenied: User: arn:aws:iam::123456789012:role/LaunchpadDeploymentRole
is not authorized to perform: ec2:CreateVpc on resource arn:aws:ec2:us-east-1:123456789012:vpc/*
  File "/app/api/services/terraform_worker.py", line 402, in provision`;

const LEAKED_TOKENS = [
    '123456789012',
    'arn:aws:iam',
    'arn:aws:ec2',
    'LaunchpadDeploymentRole',
    'terraform_worker.py',
    'GetCallerIdentity',
    'StatusCode: 403',
];

test('summarizeInfraError returns undefined for empty input', () => {
    assert.equal(summarizeInfraError(undefined), undefined);
    assert.equal(summarizeInfraError(''), undefined);
    assert.equal(summarizeInfraError('   \n  '), undefined);
});

test('summarizeInfraError never echoes any token from the raw error', () => {
    const summary = summarizeInfraError(RAW_LEAKY_ERROR);
    assert.ok(summary, 'expected a summary');
    for (const token of LEAKED_TOKENS) {
        assert.ok(!summary!.includes(token), `summary leaked sensitive token: ${token}`);
    }
});

test('summarizeInfraError classifies known failure categories', () => {
    assert.match(summarizeInfraError('AccessDenied assume role')!, /onboarding script/i);
    assert.match(summarizeInfraError('InvalidClientTokenId: token expired')!, /expired/i);
    assert.match(summarizeInfraError('LimitExceeded: quota reached')!, /limit/i);
    assert.match(
        summarizeInfraError('context deadline exceeded / timed out')!,
        /timed out|temporary/i,
    );
    assert.match(summarizeInfraError('CIDR block overlaps existing VPC')!, /networking|CIDR/i);
    assert.match(summarizeInfraError('something totally unexpected happened')!, /full log/i);
});

// Realistic raw EKS failures — the cluster ARN, account id, OIDC issuer, node role names and the
// cluster API endpoint are all directly sensitive and must never reach an inbox.
const RAW_EKS_ERRORS: Record<string, string> = {
    access_entry: `Error: creating EKS Access Entry (infra-019ccc43): operation error EKS: CreateAccessEntry,
StatusCode: 403, api error AccessDeniedException: User: arn:aws:iam::123456789012:role/LaunchpadDeploymentRole
is not authorized to perform: eks:CreateAccessEntry on resource: arn:aws:eks:us-east-1:123456789012:cluster/infra-019ccc43`,
    cluster_timeout: `Error: waiting for EKS Cluster (infra-019ccc43) create: timeout while waiting for state to become 'ACTIVE'
(last state: 'CREATING') arn:aws:eks:us-east-1:123456789012:cluster/infra-019ccc43
oidc issuer https://oidc.eks.us-east-1.amazonaws.com/id/ABCDEF0123456789ABCDEF0123456789`,
    addon_cni: `Error: creating EKS Add-On (infra-019ccc43:vpc-cni): InvalidParameterException: Addon version specified is not
supported, node role arn:aws:iam::123456789012:role/infra-019ccc43-node-role, amazon-vpc-cni enable-network-policy-controller`,
    node_capacity: `karpenter nodepool general-purpose FailedScheduling: 0/0 nodes are available:
insufficient capacity in availability zone us-east-1a for node role arn:aws:iam::123456789012:role/infra-019ccc43-node-role`,
    k8s_api_unreachable: `Unable to connect to the server: dial tcp: lookup 0123456789abcdef.gr7.us-east-1.eks.amazonaws.com: i/o timeout
  File "/app/api/services/eks_bootstrap.py", line 88, in _wait_for_alb`,
};

const EKS_LEAKED_TOKENS = [
    '123456789012',
    'arn:aws:iam',
    'arn:aws:eks',
    'infra-019ccc43',
    'LaunchpadDeploymentRole',
    'oidc.eks.us-east-1.amazonaws.com',
    '0123456789abcdef.gr7.us-east-1.eks.amazonaws.com',
    'eks_bootstrap.py',
];

test('summarizeInfraError classifies EKS failure buckets', () => {
    assert.match(
        summarizeInfraError(RAW_EKS_ERRORS.access_entry)!,
        /Kubernetes cluster in your AWS account/,
    );
    assert.match(summarizeInfraError(RAW_EKS_ERRORS.cluster_timeout)!, /did not finish creating/);
    assert.match(summarizeInfraError(RAW_EKS_ERRORS.addon_cni)!, /cluster component failed/);
    assert.match(
        summarizeInfraError(RAW_EKS_ERRORS.node_capacity)!,
        /compute capacity for the cluster nodes/,
    );
    assert.match(
        summarizeInfraError(RAW_EKS_ERRORS.k8s_api_unreachable)!,
        /could not reach your Kubernetes cluster's API/,
    );
});

test('an unmatched EKS error falls through to the generic fallback', () => {
    const raw = `Error: unexpected EKS reconcile wobble for arn:aws:eks:us-east-1:123456789012:cluster/infra-019ccc43`;
    const summary = summarizeInfraError(raw);
    assert.match(summary!, /full log/);
    assert.ok(!summary!.includes('arn:aws:eks'), 'fallback leaked the cluster ARN');
    assert.ok(!summary!.includes('123456789012'), 'fallback leaked the account id');
});

test('a non-EKS cluster timeout does not get Kubernetes guidance', () => {
    // The ECS/RDS/DocDB paths also say "cluster". Before the EKS buckets required an
    // `eks` token, this matched the Kubernetes cluster-creation bucket and told the
    // customer to wait 20 minutes for a control plane they never asked for.
    const raw =
        'Error: creating RDS DocDB Cluster: operation timed out waiting for cluster creation';
    const summary = summarizeInfraError(raw);
    assert.ok(summary, 'expected a summary');
    assert.ok(
        !/Kubernetes/i.test(summary!),
        `non-EKS cluster timeout received Kubernetes guidance: ${summary}`,
    );
});

test('no EKS bucket echoes raw error text into the summary or the email', () => {
    for (const [bucket, raw] of Object.entries(RAW_EKS_ERRORS)) {
        const summary = summarizeInfraError(raw);
        assert.ok(summary, `expected a summary for ${bucket}`);
        const html = getInfraEmailTemplate(
            'provision_failure',
            'prod-infra',
            'Mohamed',
            raw,
            'https://app.example.com/dashboard',
        );
        for (const token of EKS_LEAKED_TOKENS) {
            assert.ok(!summary!.includes(token), `${bucket} summary leaked: ${token}`);
            assert.ok(!html.includes(token), `${bucket} email leaked: ${token}`);
        }
        assert.ok(!html.includes('<pre'), `${bucket} email must not render a raw error dump`);
    }
});

test('a failure email never contains raw AWS identifiers or stack traces', () => {
    const html = getInfraEmailTemplate(
        'provision_failure',
        'prod-infra',
        'Mohamed',
        RAW_LEAKY_ERROR,
        'https://app.example.com/dashboard',
    );
    for (const token of LEAKED_TOKENS) {
        assert.ok(!html.includes(token), `email leaked sensitive token: ${token}`);
    }
    assert.ok(!html.includes('<pre'), 'email must not render a raw error dump');
    assert.match(html, /What to do next/);
    assert.match(html, /onboarding script/i);
    assert.match(html, /prod-infra/);
});

test('a success email shows Active status and no guidance card', () => {
    const html = getInfraEmailTemplate(
        'provision_success',
        'prod-infra',
        'Mohamed',
        undefined,
        undefined,
    );
    assert.match(html, /Active/);
    assert.match(html, /prod-infra/);
    assert.ok(!html.includes('What to do next'), 'success email should have no guidance card');
});

test('dashboard CTA renders only when a url is provided', () => {
    const withUrl = getInfraEmailTemplate(
        'provision_success',
        'x',
        'y',
        undefined,
        'https://app.example.com/dashboard',
    );
    const without = getInfraEmailTemplate('provision_success', 'x', 'y', undefined, undefined);
    assert.match(withUrl, /app\.example\.com\/dashboard/);
    assert.ok(
        !without.includes('href="undefined"'),
        'must not render a broken CTA when url is absent',
    );
});

test('clean subjects read naturally', () => {
    assert.equal(
        getInfraEmailSubject('provision_success', 'prod'),
        'Your environment "prod" is live',
    );
    assert.equal(
        getInfraEmailSubject('provision_failure', 'prod'),
        'Action needed: "prod" couldn\'t be provisioned',
    );
    assert.equal(getInfraEmailSubject('destroy_success', 'prod'), '"prod" has been torn down');
    assert.equal(
        getInfraEmailSubject('provision_success', undefined),
        'Your environment "your environment" is live',
    );
});

// InfraEvent's Record<InfraEvent, ...> declarations (EVENT_LABELS, CLEAN_SUBJECTS) enforce
// exhaustiveness at compile time — this locks in that ALL_INFRA_EVENTS itself stays in sync,
// and that every event renders without throwing.
test('every InfraEvent renders a template and a subject without throwing', () => {
    assert.equal(ALL_INFRA_EVENTS.length, 8);
    for (const event of ALL_INFRA_EVENTS) {
        const html = getInfraEmailTemplate(event, 'infra-x', 'Mohamed', 'boom', undefined, 'db-x');
        assert.ok(html.length > 0, `expected non-empty html for ${event}`);
        const subject = getInfraEmailSubject(event, 'infra-x', 'db-x');
        assert.ok(subject.length > 0, `expected non-empty subject for ${event}`);
    }
});

test('database events show the database name and engine-specific guidance', () => {
    const html = getInfraEmailTemplate(
        'database_create_failure',
        'prod-infra',
        'Mohamed',
        'DBInstanceAlreadyExists: db already exists',
        'https://app.example.com/dashboard',
        'primary-db',
    );
    assert.match(html, /primary-db/);
    assert.match(html, /prod-infra/);
    assert.match(html, /already exists/i);
});

test('a crafted infra/database/user name is HTML-escaped, not injected', () => {
    const evilName = '<img src=x onerror=alert(1)>';
    const html = getInfraEmailTemplate(
        'database_create_success',
        evilName,
        evilName,
        undefined,
        undefined,
        evilName,
    );
    assert.ok(
        !html.includes('<img src=x onerror=alert(1)>'),
        'raw markup must not appear unescaped',
    );
    assert.ok(html.includes('&lt;img'), 'expected the escaped form to be present');
});

test('database subject line references the database, not the infra', () => {
    assert.equal(
        getInfraEmailSubject('database_create_success', 'prod-infra', 'primary-db'),
        'Your database "primary-db" is live',
    );
    assert.equal(
        getInfraEmailSubject('database_delete_failure', 'prod-infra', 'primary-db'),
        'Action needed: database "primary-db" couldn\'t be deleted',
    );
});

test('auth email carries the magic link and user name', () => {
    const url = 'https://gw.example.com/auth/authenticate-with-otp?email=a@b.com&otp=123456';
    const html = getAuthEmailTemplate(url, 'Mohamed');
    assert.match(html, /Welcome aboard, Mohamed/);
    assert.ok(html.includes(url), 'auth email must contain the sign-in url');
    assert.match(html, /Activate my account/);
});

test('forgot-password email shows the reset code', () => {
    const html = getForgotPasswordTemplate('482915', 'Mohamed');
    assert.match(html, /482915/);
    assert.match(html, /Reset your password/);
});
