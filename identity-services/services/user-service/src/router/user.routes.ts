import { Router } from 'express';
import { GetUserById, SearchUsers } from '@/controllers/user.controller';

export const userRouter: Router = Router();

userRouter.get('/:userId', GetUserById);
userRouter.get('/', SearchUsers);
