import pino from "pino";
import type { Logger, LoggerOptions } from "pino";

type CreateLoggerOptions = LoggerOptions & {
    name: string
}

export type AppLogger = Logger;

export const CreateLogger = (options: CreateLoggerOptions): Logger => {
    const { name, ...rest } = options

    const transport = process.env.NODE_ENV === "development" ? {
        target: "pino-pretty",
        options: {
            colorize: true,
            translateTime: "SYS:standard"
        },
    } : undefined;

    return pino(
        {
            name,
            transport,
            level: process.env.LOG_LEVEL || "info",
            ...rest
        }
    )
}