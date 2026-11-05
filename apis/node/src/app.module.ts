import { Inject, Module, type OnApplicationShutdown } from '@nestjs/common';
import pg from 'pg';
import { AppController } from './app.controller.js';
import { PG_POOL } from './db.js';

@Module({
  controllers: [AppController],
  providers: [
    {
      provide: PG_POOL,
      useFactory: () => new pg.Pool({ connectionString: process.env.DATABASE_URL, max: 20 }),
    },
  ],
})
export class AppModule implements OnApplicationShutdown {
  constructor(@Inject(PG_POOL) private readonly pool: pg.Pool) {}

  async onApplicationShutdown() {
    await this.pool.end();
  }
}
