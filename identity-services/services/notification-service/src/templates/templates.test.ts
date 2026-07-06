import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    getInfraEmailTemplate,
    getInfraEmailSubject,
    summarizeInfraError,
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
