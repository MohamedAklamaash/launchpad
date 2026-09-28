import { NextFunction, Request, Response } from 'express';
import { userService } from '@/service/user.service';
import { HttpError } from '@launchpad/common';
import { resolveCaller } from '@/utils/resolve-caller';
import type { User } from '@/types/user.type';

// Below this, a search term is too broad to be a deliberate lookup (an invite flow
// typing a name or email) and turns into cheap enumeration.
const MIN_SEARCH_QUERY_LENGTH = 3;
// How many candidates we pull from the DB by name/email match before narrowing to the
// caller's own infras — wide enough that the post-filter below rarely starves the
// final, smaller result set.
const SEARCH_CANDIDATE_LIMIT = 50;
const SEARCH_RESULT_LIMIT = 10;

export interface UserSearchResult {
    user_id: string;
    user_name: string;
    email: string;
    profile_url?: string;
}

const toSearchResult = (user: User): UserSearchResult => ({
    user_id: user.user_id,
    user_name: user.user_name,
    email: user.email,
    profile_url: user.profile_url,
});

export const GetUserById = async (req: Request, res: Response, next: NextFunction) => {
    try {
        const caller = resolveCaller(req);
        const userId = req.params.userId as string;
        if (!userId) {
            throw new HttpError(400, 'User ID is required');
        }
        // A user may look up only their own record. There is no admin carve-out here:
        // member lists for an org already exist via auth-service's invited-users
        // endpoint, which is scoped to infras the caller owns.
        if (caller.sub !== userId) {
            throw new HttpError(403, 'You may only view your own profile');
        }
        const user = await userService.getUserById(userId);
        res.status(200).json(user);
    } catch (error) {
        next(error);
    }
};

// Scoped to inviting a member into one of the caller's own infras: matches are further
// restricted to users who share at least one infra with the caller, so an authenticated
// account in one tenant cannot enumerate users of another tenant. No product feature
// calls this endpoint yet (verified against the frontend and the other services), so
// this scoping is forward-looking rather than a fix for a broken caller.
export const SearchUsers = async (req: Request, res: Response, next: NextFunction) => {
    try {
        const caller = resolveCaller(req);
        const query = (req.query.q as string) ?? '';
        if (query.trim().length < MIN_SEARCH_QUERY_LENGTH) {
            throw new HttpError(
                400,
                `Search query 'q' must be at least ${MIN_SEARCH_QUERY_LENGTH} characters`,
            );
        }

        let callerInfraIds: string[];
        try {
            callerInfraIds = (await userService.getUserById(caller.sub)).infra_id;
        } catch (error) {
            if (error instanceof HttpError && error.statusCode === 404) {
                // Caller's own record hasn't replicated yet (or never will) — nothing to
                // scope search results to, so there is nothing safe to return.
                res.status(200).json([]);
                return;
            }
            throw error;
        }

        const candidates = await userService.searchUsers({
            query,
            limit: SEARCH_CANDIDATE_LIMIT,
            excludeIds: [caller.sub],
        });
        const scoped = candidates
            .filter((user) => user.infra_id.some((infraId) => callerInfraIds.includes(infraId)))
            .slice(0, SEARCH_RESULT_LIMIT)
            .map(toSearchResult);

        res.status(200).json(scoped);
    } catch (error) {
        next(error);
    }
};
