import 'reflect-metadata';
import { existsSync } from 'fs';
import * as path from 'path';
import { Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { NestExpressApplication } from '@nestjs/platform-express';
import { AppModule } from './app.module';

// Load a .env file (Node ≥20.6) so the dashboard can be configured from a file
// instead of exporting env vars in the shell. Looked up in the launch directory.
try {
  const envFile = path.resolve(process.cwd(), '.env');
  const loadEnvFile = (process as unknown as { loadEnvFile?: (p: string) => void }).loadEnvFile;
  if (typeof loadEnvFile === 'function' && existsSync(envFile)) loadEnvFile(envFile);
} catch {
  /* non-fatal — fall back to the process environment */
}

async function bootstrap() {
  const app = await NestFactory.create<NestExpressApplication>(AppModule, { logger: ['log', 'warn', 'error'] });
  // Vue dev server runs on a different origin in dev.
  app.enableCors({ origin: true, credentials: true });

  // Serve the built web UI (apps/web/dist) when present, so production is a
  // single process: dashboard at http://127.0.0.1:<port>, API under /api.
  const webDir = path.resolve(__dirname, '..', '..', 'web', 'dist');
  if (existsSync(webDir)) {
    app.useStaticAssets(webDir);
    new Logger('bootstrap').log(`Serving web UI from ${webDir}`);
  }

  const port = Number(process.env.PORT ?? 4000);
  await app.listen(port);
  new Logger('bootstrap').log(`Hermes dashboard API listening on http://127.0.0.1:${port}`);
}

void bootstrap();
