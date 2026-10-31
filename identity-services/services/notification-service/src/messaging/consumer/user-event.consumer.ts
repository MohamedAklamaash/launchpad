import { Worker, Job, UnrecoverableError } from 'bullmq';
import {
    AUTHENTICATE_INVITED_USER_EVENT,
    NOTIFICATION_EVENT_QUEUE,
    FORGOT_PASSWORD_EVENT,
    INFRA_NOTIFICATION_EVENT,
    ORG_INVITATION_EMAIL_EVENT,
    type OrgInvitationEmailJob,
} from '@launchpad/common';
import { getAuthEmailTemplate } from '@/templates/auth-email.template';
import { getForgotPasswordTemplate } from '@/templates/forgot-password.template';
import { getInfraEmailTemplate, getInfraEmailSubject } from '@/templates/infra-email.template';
import {
    buildInvitationAcceptUrl,
    getInvitationSubject,
    getInvitationTemplate,
} from '@/templates/invitation.template';
import { sendMail } from '@/service/mail.service';
import { notificationService } from '@/service/notification.service';
import { env } from '@/config/env';
import { redisConfig } from '@/client/redis';
import { logger } from '@/utils/logger';

export const userEventsWorker = new Worker(
    NOTIFICATION_EVENT_QUEUE,
    async (job: Job) => {
        logger.info(
            { job_id: job.id, job_name: job.name, attempt: job.attemptsMade + 1 },
            'Processing notification job',
        );

        switch (job.name) {
            case AUTHENTICATE_INVITED_USER_EVENT: {
                const { user_id, email, otp, infra_id, source, user_name } = job.data;

                if (!email || !otp) {
                    throw new UnrecoverableError(
                        `Job ${job.id} (${job.name}): missing required fields email or otp`,
                    );
                }

                // encodeURIComponent both — an unencoded '+', '&', or '#' in an email
                // address would otherwise break the query string (truncating or
                // misparsing the otp param) or get silently mangled by an email client.
                const authUrl = `${env.GATEWAY_SERVICE_URL}/auth/authenticate-with-otp?email=${encodeURIComponent(email)}&otp=${encodeURIComponent(otp)}`;
                const emailHtml = getAuthEmailTemplate(authUrl, user_name);

                logger.info({ job_id: job.id, email, user_id }, 'Sending auth email');
                await sendMail(email, 'Authenticate to Launchpad', emailHtml);

                logger.info({ job_id: job.id, user_id }, 'DB write: saving notification record');
                await notificationService.save({
                    user_id,
                    email,
                    user_name,
                    infra_id,
                    source,
                });
                logger.info({ job_id: job.id, user_id }, 'DB write: notification saved');
                break;
            }

            case FORGOT_PASSWORD_EVENT: {
                const { email: fpEmail, otp: fpOtp, user_name: fpUserName } = job.data;

                if (!fpEmail || !fpOtp) {
                    throw new UnrecoverableError(
                        `Job ${job.id} (${job.name}): missing required fields email or otp`,
                    );
                }

                const fpEmailHtml = getForgotPasswordTemplate(fpOtp, fpUserName);
                logger.info({ job_id: job.id, email: fpEmail }, 'Sending forgot-password email');
                await sendMail(fpEmail, 'Reset Your Launchpad Password', fpEmailHtml);
                break;
            }

            case INFRA_NOTIFICATION_EVENT: {
                const {
                    user_id,
                    user_name,
                    email,
                    infra_id,
                    infra_name,
                    event,
                    error,
                    database_name,
                    manual_step_url,
                } = job.data;

                if (!email || !event) {
                    throw new UnrecoverableError(
                        `Job ${job.id} (${job.name}): missing required fields email or event`,
                    );
                }

                const html = getInfraEmailTemplate(
                    event,
                    infra_name,
                    user_name,
                    error,
                    env.DASHBOARD_URL,
                    database_name,
                    manual_step_url,
                );
                const subject = getInfraEmailSubject(event, infra_name, database_name);

                logger.info({ job_id: job.id, email, event }, 'Sending infra notification email');
                await sendMail(email, subject, html);

                await notificationService.save({
                    user_id,
                    email,
                    user_name,
                    infra_id,
                    source: event,
                });
                break;
            }

            case ORG_INVITATION_EMAIL_EVENT: {
                // The job carries a bearer token. Log the job id only: never the payload,
                // the token, the link, or the recipient.
                const invitation = job.data as Partial<OrgInvitationEmailJob>;
                const acceptUrl =
                    invitation.token && env.DASHBOARD_URL
                        ? buildInvitationAcceptUrl(env.DASHBOARD_URL, invitation.token)
                        : undefined;
                const expiresAt = new Date(invitation.expires_at ?? '');

                if (!invitation.email || !acceptUrl || Number.isNaN(expiresAt.getTime())) {
                    throw new UnrecoverableError(
                        `Job ${job.id} (${job.name}): missing recipient, token, expiry or a usable DASHBOARD_URL`,
                    );
                }

                const html = getInvitationTemplate({
                    organizationName: invitation.organization_name ?? '',
                    inviterName: invitation.inviter_name ?? '',
                    role: invitation.role ?? '',
                    expiresAt,
                    acceptUrl,
                });
                logger.info({ job_id: job.id }, 'Sending organization invitation email');
                await sendMail(
                    invitation.email,
                    getInvitationSubject(invitation.organization_name ?? ''),
                    html,
                );
                break;
            }

            default:
                logger.warn(
                    { job_id: job.id, job_name: job.name },
                    'Unknown notification job type — skipping',
                );
                break;
        }
    },
    {
        connection: redisConfig,
    },
);

// Required: surface failures — previously all job failures were silently swallowed
userEventsWorker.on('failed', (job, err) => {
    logger.error(
        {
            job_id: job?.id,
            job_name: job?.name,
            attempts_made: job?.attemptsMade,
            err,
        },
        'Notification job permanently failed',
    );
});

userEventsWorker.on('error', (err) => {
    logger.error({ err }, 'BullMQ worker connection error');
});
