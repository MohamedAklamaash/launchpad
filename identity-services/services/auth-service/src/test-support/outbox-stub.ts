import { mock } from 'node:test';

// For tests of services that enqueue org events but do not exercise the dispatcher: records
// each outbox row and hands it straight to the (mocked) publisher so existing publish
// assertions keep working. The dispatcher itself is tested in org-event-outbox.test.ts.
export const stubOutbox = async () => {
    const { OrgEventOutbox } = await import('@/db');
    const { publishOutboxEvent } = await import('@/messaging/producer/org-events.message');
    const rows: { routing_key: string; payload: Record<string, unknown> }[] = [];
    const spy = mock.method(OrgEventOutbox, 'create', async (attrs: (typeof rows)[number]) => {
        rows.push(attrs);
        publishOutboxEvent(attrs.routing_key, attrs.payload, new Date());
        return attrs;
    });
    return { rows, restore: () => spy.mock.restore() };
};
