import { test } from 'node:test';
import assert from 'node:assert/strict';
import { authorizeMemberRemoval } from '@/utils/role';

test('owner of one infra cannot remove a member from an infra they do not own', () => {
    const result = authorizeMemberRemoval(['infra-A'], { 'infra-B': 'user' }, ['infra-B']);
    assert.equal(result.error?.status, 403);
    assert.deepEqual(result.toRemove, []);
});

test('mixed request fails closed when any requested infra is unowned', () => {
    const result = authorizeMemberRemoval(['infra-A'], { 'infra-A': 'user', 'infra-B': 'user' }, [
        'infra-A',
        'infra-B',
    ]);
    assert.equal(result.error?.status, 403);
    assert.deepEqual(result.toRemove, []);
});

test('empty request only touches the caller-owned orgs, never a member other orgs', () => {
    const result = authorizeMemberRemoval(
        ['infra-A'],
        { 'infra-A': 'user', 'infra-C': 'admin' },
        [],
    );
    assert.equal(result.error, undefined);
    assert.deepEqual(result.toRemove, ['infra-A']);
});

test('removal from an owned infra the member belongs to is authorized', () => {
    const result = authorizeMemberRemoval(['infra-A', 'infra-B'], { 'infra-A': 'user' }, [
        'infra-A',
    ]);
    assert.equal(result.error, undefined);
    assert.deepEqual(result.toRemove, ['infra-A']);
});

test('owned infra the member is not part of yields a not-found, not a silent no-op', () => {
    const result = authorizeMemberRemoval(['infra-A', 'infra-B'], { 'infra-A': 'user' }, [
        'infra-B',
    ]);
    assert.equal(result.error?.status, 404);
    assert.deepEqual(result.toRemove, []);
});
