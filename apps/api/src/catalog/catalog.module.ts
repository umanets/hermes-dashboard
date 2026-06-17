import { Module } from '@nestjs/common';
import { HermesConfigModule } from '../hermes-config/hermes-config.module';
import { InstallationModule } from '../installation/installation.module';
import { AppleAuthService } from './apple-auth.service';
import { CatalogController } from './catalog.controller';
import { CatalogService } from './catalog.service';
import { IngestJobService } from './ingest-job.service';
import { InstallJobService } from './install-job.service';
import { ModuleInstallerService } from './module-installer.service';

@Module({
  imports: [InstallationModule, HermesConfigModule],
  controllers: [CatalogController],
  providers: [CatalogService, ModuleInstallerService, InstallJobService, IngestJobService, AppleAuthService],
  exports: [CatalogService],
})
export class CatalogModule {}
