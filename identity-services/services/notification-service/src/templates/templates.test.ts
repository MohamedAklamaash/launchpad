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
