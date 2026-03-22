import { env } from '@/config/env';
import nodemailer from 'nodemailer';
import { logger } from '@/utils/logger';

const transporter = nodemailer.createTransport({
    host: env.MAIL_HOST,
    port: 465, // Force SSL
    secure: true, // Force SSL
    auth: {
        user: env.MAIL_USER,
        pass: env.MAIL_APP_PASSWORD.trim(),
    },
    logger: true,
    debug: true,
});

transporter.verify((error, _success) => {
    if (error) {
        logger.error({ error }, 'Mail server connection failed');
    } else {
        logger.info('Mail server is ready to take our messages');
    }
});

export const sendMail = async (to: string, subject: string, html: string) => {
    try {
        const info = await transporter.sendMail({
            from: `Launchpad <${env.FROM_MAIL}>`,
            to,
            subject,
            html,
        });

        logger.info({ messageId: info.messageId }, 'Email sent successfully');
    } catch (error) {
        logger.error({ error }, 'Failed to send email');
    }
};
