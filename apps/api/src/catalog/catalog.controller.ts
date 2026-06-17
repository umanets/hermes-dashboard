import { BadRequestException, Body, Controller, Get, Param, Post } from '@nestjs/common';
import { z } from 'zod';
import { AppleAuthService } from './apple-auth.service';
import { CatalogService } from './catalog.service';
import { IngestJobService } from './ingest-job.service';
import { InstallJobService } from './install-job.service';
import { ModuleInstallerService } from './module-installer.service';

const InstallSchema = z.object({
  env: z.record(z.string()).default({}),
  tools: z.array(z.string()).optional(),
});
const IngestSchema = z.object({ dir: z.string().min(1), vault: z.string().optional() });
const AppleTokenSchema = z.object({ token: z.string().min(1), server: z.string().optional() });

@Controller('api/catalog')
export class CatalogController {
  constructor(
    private readonly catalog: CatalogService,
    private readonly installer: ModuleInstallerService,
    private readonly jobs: InstallJobService,
    private readonly ingestJobs: IngestJobService,
    private readonly apple: AppleAuthService,
  ) {}

  /** Apple Music: start the host browser authorize flow; the captured token is auto-written. */
  @Post('apple-music/authorize')
  appleAuthorize() {
    return this.apple.start();
  }

  /** Poll the authorize flow (browser sign-in can take a minute). */
  @Get('apple-music/authorize-jobs/:jobId')
  appleAuthorizeJob(@Param('jobId') jobId: string) {
    return this.apple.getJob(jobId);
  }

  /** Apple Music: write a pasted Music User Token into the selected install + reload. */
  @Post('apple-music/token')
  appleSetToken(@Body() body: unknown) {
    const parsed = AppleTokenSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.apple.setToken(parsed.data.token, parsed.data.server);
  }

  @Get()
  list() {
    return this.catalog.list();
  }

  /** Install job status (poll this after starting an install). */
  @Get('install-jobs/:jobId')
  jobStatus(@Param('jobId') jobId: string) {
    return this.jobs.get(jobId);
  }

  /** Ingest job status (poll this after starting an ingest). */
  @Get('ingest-jobs/:jobId')
  ingestStatus(@Param('jobId') jobId: string) {
    return this.ingestJobs.get(jobId);
  }

  @Get(':id')
  get(@Param('id') id: string) {
    return this.catalog.get(id);
  }

  @Get(':id/credential-form')
  credentialForm(@Param('id') id: string) {
    return this.installer.credentialForm(id);
  }

  /** Start the turnkey install pipeline (copy → venv → deps → config → cron). */
  @Post(':id/install')
  install(@Param('id') id: string, @Body() body: unknown) {
    const parsed = InstallSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.jobs.start(id, parsed.data.env, parsed.data.tools);
  }

  /** Start a bulk-ingest of a folder into the module's store. */
  @Post(':id/ingest')
  ingest(@Param('id') id: string, @Body() body: unknown) {
    const parsed = IngestSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.ingestJobs.start(id, parsed.data.dir, parsed.data.vault);
  }
}
