import { test } from 'node:test';
import assert from 'node:assert/strict';
import { accountIdForEmail, normalizeEmail } from '@/utils/account-id';

test('normalizeEmail trims and lowercases, nothing else', () => {
    assert.equal(normalizeEmail('  Foo@Example.COM  '), 'foo@example.com');
    assert.equal(normalizeEmail('foo+tag@example.com'), 'foo+tag@example.com');
});

test('accountIdForEmail is deterministic for the same normalized email', () => {
    const first = accountIdForEmail('Foo@Example.com');
    const second = accountIdForEmail('  foo@example.com  ');
    assert.equal(first, second);
});

test('accountIdForEmail differs for different emails', () => {
    assert.notEqual(accountIdForEmail('a@example.com'), accountIdForEmail('b@example.com'));
});

test('accountIdForEmail always returns a well-formed uuid', () => {
    const id = accountIdForEmail('a@example.com');
    assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
});
