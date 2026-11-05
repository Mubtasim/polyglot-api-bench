import { type ArgumentsHost, Catch, type ExceptionFilter, HttpException } from '@nestjs/common';
import type { FastifyReply } from 'fastify';

/** Turns every error into the contract's body: `{"error": "..."}`. */
@Catch()
export class ErrorFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost) {
    const reply = host.switchToHttp().getResponse<FastifyReply>();
    // Fastify's own errors (e.g. a body that is not valid JSON) carry statusCode.
    const status =
      exception instanceof HttpException
        ? exception.getStatus()
        : ((exception as { statusCode?: number })?.statusCode ?? 500);
    const message =
      status >= 500 ? 'internal error' : exception instanceof Error ? exception.message : 'invalid request';
    void reply.status(status).send({ error: message });
  }
}
