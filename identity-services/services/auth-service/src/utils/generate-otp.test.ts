import { test } from 'node:test';
import assert from 'node:assert/strict';
import { generateOTP } from '@/utils/generate-otp';

test('generateOTP returns a 6-digit numeric string by default', () => {
    const otp = generateOTP();
    assert.equal(otp.length, 6);
    assert.match(otp, /^[0-9]{6}$/);
});

test('generateOTP respects a custom length', () => {
    assert.equal(generateOTP(4).length, 4);
    assert.equal(generateOTP(10).length, 10);
});

test('generateOTP rejects a non-positive length', () => {
    assert.throws(() => generateOTP(0));
    assert.throws(() => generateOTP(-1));
});

test('generateOTP produces varied values, not a fixed sequence', () => {
    const seen = new Set(Array.from({ length: 50 }, () => generateOTP()));
    // Astronomically unlikely to collide down to a handful of unique values across 50
    // draws from a CSPRNG over 10 digits — this is not a statistical strength proof, just
    // a smoke test that it isn't returning the same value every time.
    assert.ok(seen.size > 10, `expected varied OTPs, got only ${seen.size} unique values`);
});
