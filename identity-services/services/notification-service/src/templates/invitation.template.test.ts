import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    buildInvitationAcceptUrl,
    getInvitationSubject,
    getInvitationTemplate,
} from './invitation.template';

const PAYLOAD = '<script>alert(1)</script>"><img src=x onerror=alert(2)>';

const render = (overrides: Partial<Parameters<typeof getInvitationTemplate>[0]> = {}) =>
    getInvitationTemplate({
        organizationName: 'Acme',
        inviterName: 'Sam',
        role: 'developer',
        expiresAt: new Date('2030-01-01T00:00:00Z'),
        acceptUrl: 'https://app.example.com/invitations/accept#token=abc',
        ...overrides,
    });

test('the organization name and inviter name are HTML-escaped everywhere they appear', () => {
    const html = render({ organizationName: PAYLOAD, inviterName: PAYLOAD });

    assert.ok(!html.includes('<script>alert(1)</script>'));
    assert.ok(!html.includes('<img src=x'));
    assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
});

test('an unknown role is shown as a generic label, not echoed', () => {
    const html = render({ role: PAYLOAD });

    assert.ok(!html.includes('<script>'));
    assert.ok(html.includes('>Member<'));
});

test('the accept link is escaped in the href and in the visible text', () => {
    const html = render({ acceptUrl: 'https://app.example.com/x?a=1&b="><script>' });

    assert.ok(!html.includes('"><script>'));
    assert.ok(html.includes('a=1&amp;b=&quot;&gt;&lt;script&gt;'));
});

test('subject strips control characters and caps the length', () => {
    const subject = getInvitationSubject(`Acme\r\nBcc: evil@example.com${'x'.repeat(200)}`);

    assert.ok(!/[\r\n]/.test(subject));
    assert.ok(subject.length < 140);
});

test('the accept link points at the dashboard origin and carries the token in the fragment only', () => {
    const url = buildInvitationAcceptUrl('https://app.example.com/dashboard', 'a-b_c')!;
    const parsed = new URL(url);

    assert.equal(parsed.origin, 'https://app.example.com');
    assert.equal(parsed.pathname, '/invitations/accept');
    assert.equal(parsed.search, '');
    assert.equal(parsed.hash, '#token=a-b_c');
});

test('a non-http(s) or malformed DASHBOARD_URL yields no link', () => {
    assert.equal(buildInvitationAcceptUrl('javascript:alert(1)', 't'), undefined);
    assert.equal(buildInvitationAcceptUrl('not a url', 't'), undefined);
});
