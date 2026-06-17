import { Module } from '@nestjs/common';
import { CatalogModule } from '../catalog/catalog.module';
import { HermesConfigModule } from '../hermes-config/hermes-config.module';
import { InstallationModule } from '../installation/installation.module';
import { McpController } from './mcp.controller';
import { McpService } from './mcp.service';

@Module({
  imports: [InstallationModule, HermesConfigModule, CatalogModule],
  controllers: [McpController],
  providers: [McpService],
})
export class McpModule {}
