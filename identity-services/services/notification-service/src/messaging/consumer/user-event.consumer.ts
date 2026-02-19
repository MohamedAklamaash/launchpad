import { Worker, Job, UnrecoverableError } from "bullmq";
import {
    AUTHENTICATE_INVITED_USER_EVENT,
    NOTIFICATION_EVENT_QUEUE,
    FORGOT_PASSWORD_EVENT,
} from "@launchpad/common";
import { getAuthEmailTemplate } from "@/templates/auth-email.template";
import { getForgotPasswordTemplate } from "@/templates/forgot-password.template";
import { sendMail } from "@/service/mail.service";
import { notificationService } from "@/service/notification.service";
import { env } from "@/config/env";
import { redisConfig } from "@/client/redis";
import { logger } from "@/utils/logger";

export const userEventsWorker = new Worker(
    NOTIFICATION_EVENT_QUEUE,
    async (job: Job) => {
        logger.info(
            { job_id: job.id, job_name: job.name, attempt: job.attemptsMade + 1 },
            "Processing notification job",
        );

        switch (job.name) {
            case AUTHENTICATE_INVITED_USER_EVENT: {
                const { user_id, email, otp, infra_id, source, user_name } = job.data;

                if (!email || !otp) {
                    throw new UnrecoverableError(
                        `Job ${job.id} (${job.name}): missing required fields email or otp`,
                    );
                }

                const authUrl = `${env.GATEWAY_SERVICE_URL}/auth/authenticate-with-otp?email=${email}&otp=${otp}`;
                const emailHtml = getAuthEmailTemplate(authUrl, user_name);

                logger.info({ job_id: job.id, email, user_id }, "Sending auth email");
                await sendMail(email, "Authenticate to Launchpad", emailHtml);

                logger.info({ job_id: job.id, user_id }, "DB write: saving notification record");
                await notificationService.save({
                    user_id,
                    email,
                    user_name,
                    infra_id,
                    source,
                });
                logger.info({ job_id: job.id, user_id }, "DB write: notification saved");
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
                logger.info({ job_id: job.id, email: fpEmail }, "Sending forgot-password email");
                await sendMail(fpEmail, "Reset Your Launchpad Password", fpEmailHtml);
                break;
            }

            default:
                logger.warn(
                    { job_id: job.id, job_name: job.name },
                    "Unknown notification job type — skipping",
                );
                break;
        }
    },
    {
        connection: redisConfig,
    },
);

// Required: surface failures — previously all job failures were silently swallowed
userEventsWorker.on("failed", (job, err) => {
    logger.error(
        {
            job_id: job?.id,
            job_name: job?.name,
            attempts_made: job?.attemptsMade,
            err,
        },
        "Notification job permanently failed",
    );
});

userEventsWorker.on("error", (err) => {
    logger.error({ err }, "BullMQ worker connection error");
});