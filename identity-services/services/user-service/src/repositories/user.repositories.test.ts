import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { Op } from 'sequelize';

process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'x'.repeat(32);
process.env.USER_DB_URL = 'mysql://test:test@localhost:3306/test';
process.env.RABBITMQ_URL = 'amqp://guest:guest@localhost:5672/';
process.env.JWT_SECRET = 'x'.repeat(32);

const { User: UserModel } = await import('@/db');
const { userRepository } = await import('@/repositories/user.repositories');

const fakeFoundUser = {
    user_id: 'user-b',
    email: 'b@example.com',
    role: 'user',
    created_at: new Date(),
    updated_at: new Date(),
    infra_id: ['infra-1'],
    metadata: {},
    user_name: 'b',
    profile_url: undefined,
    invited_by: undefined,
};

test('searchByQuery escapes LIKE metacharacters in the query text', async () => {
    let capturedWhere: unknown;
    const findAllSpy = mock.method(UserModel, 'findAll', async (opts: { where: unknown }) => {
        capturedWhere = opts.where;
        return [fakeFoundUser];
    });

    try {
        await userRepository.searchByQuery('a%b_c\\d');
        const [{ [Op.or]: orClause }] = (
            capturedWhere as { [Op.and]: Array<Record<symbol, unknown>> }
        )[Op.and];
        const nameClause = (orClause as Array<Record<string, unknown>>)[0].user_name as Record<
            symbol,
            string
        >;
        // Every literal %, _ and \ from the caller's own input must come through
        // backslash-escaped — otherwise they act as SQL wildcards instead of literal
        // characters, defeating the point of a length-gated, specific search term.
        assert.equal(nameClause[Op.like], '%a\\%b\\_c\\\\d%');
    } finally {
        findAllSpy.mock.restore();
    }
});

test('searchByQuery filters by shared infra in SQL via JSON_CONTAINS, one per caller infra id', async () => {
    let capturedWhere: { [Op.and]: Array<{ [Op.or]?: Array<{ val?: string }> }> } | undefined;
    const findAllSpy = mock.method(
        UserModel,
        'findAll',
        async (opts: { where: typeof capturedWhere }) => {
            capturedWhere = opts.where;
            return [fakeFoundUser];
        },
    );

    const infraA = '018e1234-abcd-7000-8000-000000000001';
    const infraB = '018e1234-abcd-7000-8000-000000000002';

    try {
        await userRepository.searchByQuery('john', { infraIds: [infraA, infraB] });
        const andConditions = capturedWhere?.[Op.and] ?? [];
        // The infra clause is the one whose Op.or entries are raw SQL literals (`.val`
        // present) rather than the name/email LIKE clause's plain attribute conditions.
        const infraClause = andConditions.find((c) => c[Op.or]?.[0]?.val !== undefined);
        const literals = infraClause?.[Op.or] ?? [];
        assert.equal(literals.length, 2);
        assert.match(
            literals[0].val ?? '',
            new RegExp(`JSON_CONTAINS\\(infra_id, '"${infraA}"'\\)`),
        );
        assert.match(
            literals[1].val ?? '',
            new RegExp(`JSON_CONTAINS\\(infra_id, '"${infraB}"'\\)`),
        );
    } finally {
        findAllSpy.mock.restore();
    }
});

test('searchByQuery drops a non-UUID infra id rather than splicing it into raw SQL', async () => {
    const findAllSpy = mock.method(UserModel, 'findAll', async () => [fakeFoundUser]);

    try {
        const result = await userRepository.searchByQuery('john', {
            infraIds: ["'; DROP TABLE users; --"],
        });
        // No valid infra id survives filtering, so the query must short-circuit to no
        // results rather than ever building SQL out of the rejected value.
        assert.deepEqual(result, []);
        assert.equal(findAllSpy.mock.callCount(), 0);
    } finally {
        findAllSpy.mock.restore();
    }
});
