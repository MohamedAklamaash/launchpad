import { Queue } from "bullmq";
import { redisConfig } from "@/client/redis";
import { NOTIFICATION_EVENT_QUEUE } from "@launchpad/common";

// this queue is used to send event to the notification service
export const userAuthenticationQueue = new Queue(NOTIFICATION_EVENT_QUEUE, {
    connection: redisConfig,
});