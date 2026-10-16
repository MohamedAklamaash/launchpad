import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ORG_NAMESPACE, personalOrgId } from '@/utils/org-id';

const VECTOR_1 = '11111111-1111-1111-1111-111111111111';
const VECTOR_1_EXPECTED = '622fe5e9-2e23-53f5-a533-9fb6a4a8bd14';

const VECTOR_2 = '019f2c2b-9b19-71a9-8f3e-cd81d13effb9';
const VECTOR_2_EXPECTED = '9762168d-fbdc-5ee1-aa27-39ad30b6223f';

test('personalOrgId matches the known vector shared with shared/utils/org_id.py', () => {
    assert.equal(personalOrgId(VECTOR_1), VECTOR_1_EXPECTED);
});

test('personalOrgId matches a second known vector', () => {
    assert.equal(personalOrgId(VECTOR_2), VECTOR_2_EXPECTED);
});

test('personalOrgId is deterministic', () => {
    assert.equal(personalOrgId(VECTOR_1), personalOrgId(VECTOR_1));
});

test('personalOrgId canonicalizes case before hashing', () => {
    assert.equal(personalOrgId(VECTOR_1.toUpperCase()), VECTOR_1_EXPECTED);
});

test('personalOrgId trims whitespace before hashing', () => {
    assert.equal(personalOrgId(`  ${VECTOR_1}  `), VECTOR_1_EXPECTED);
});

test('different legacy ids get different org ids', () => {
    assert.notEqual(personalOrgId(VECTOR_1), personalOrgId(VECTOR_2));
});

test('ORG_NAMESPACE is fixed, matching shared/utils/org_id.py', () => {
    assert.equal(ORG_NAMESPACE, '4c900569-d11e-46e2-b34c-cd677a57ca73');
});

test('personalOrgId rejects a malformed id instead of hashing it anyway', () => {
    assert.throws(() => personalOrgId('not-a-uuid'));
});
