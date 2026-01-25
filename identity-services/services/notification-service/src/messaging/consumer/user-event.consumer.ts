import { Worker, Job } from "bullmq";
import { AUTHENTICATE_INVITED_USER_EVENT, NOTIFICATION_EVENT_QUEUE, FORGOT_PASSWORD_EVENT } from "@launchpad/common";
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
        logger.info({ job: job.name, data: job.data }, 'Processing notification job');
        switch (job.name) {
            case AUTHENTICATE_INVITED_USER_EVENT:
                const { user_id, email, otp, infra_id, source, user_name } = job.data;
                const authUrl = `${env.GATEWAY_SERVICE_URL}/auth/authenticate-with-otp?email=${email}&otp=${otp}`;
                const emailHtml = getAuthEmailTemplate(authUrl, user_name);

                await sendMail(email, "Authenticate to Launchpad", emailHtml);

                await notificationService.save({
                    user_id,
                    email,
                    user_name,
                    infra_id,
                    source
                });
                break;
            case FORGOT_PASSWORD_EVENT:
                const { email: fpEmail, otp: fpOtp, user_name: fpUserName } = job.data;
                const fpEmailHtml = getForgotPasswordTemplate(fpOtp, fpUserName);
                await sendMail(fpEmail, "Reset Your Launchpad Password", fpEmailHtml);
                break;
            default:
                break;
        }
    },
    {
        connection: redisConfig
    }
)