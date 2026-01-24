import { env } from '@/config/env';
import { Resend } from 'resend';
import { logger } from '@/utils/logger';

const resend = new Resend(env.RESEND_API_KEY);

export const sendMail = async (to: string, subject: string, html: string) => {
    const { data, error } = await resend.emails.send({
        from: `Launchpad <${env.FROM_MAIL}>`,
        to: [to],
        subject,
        html,
    });

    if (error) {
        return logger.error({ error });
    }

    logger.info({ data });
}
