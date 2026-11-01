import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseFlagDefaultOn } from './flag';

test('on when unset, empty, or anything but an explicit off value', () => {
    for (const v of [undefined, null, '', '  ', 'true', 'True', 'TRUE', '1', 'yes', true]) {
        assert.equal(parseFlagDefaultOn(v), true, String(v));
    }
});

test('off only for false/0/no/off in any case', () => {
    for (const v of ['false', 'False', 'FALSE', ' false ', '0', 'no', 'Off', false]) {
        assert.equal(parseFlagDefaultOn(v), false, String(v));
    }
});
