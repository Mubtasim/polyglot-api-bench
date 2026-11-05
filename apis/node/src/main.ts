// Serves the benchmark contract (docs/design.md, section 2) with NestJS on Fastify.
import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { FastifyAdapter, type NestFastifyApplication } from '@nestjs/platform-fastify';
import { AppModule } from './app.module.js';
import { ErrorFilter } from './error.filter.js';

const app = await NestFactory.create<NestFastifyApplication>(
  AppModule,
  new FastifyAdapter({ bodyLimit: 10 * 1024 * 1024 }),
  { logger: ['error', 'warn'] }, // no request logging
);
app.useGlobalFilters(new ErrorFilter());
app.enableShutdownHooks();

const port = Number(process.env.PORT ?? 8080);
await app.listen(port, '0.0.0.0');
console.log(`Node.js (NestJS + Fastify) listening on :${port}`);
