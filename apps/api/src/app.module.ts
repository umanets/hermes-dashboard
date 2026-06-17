import { Module } from '@nestjs/common';
import { CatalogModule } from './catalog/catalog.module';
import { CoreModule } from './core/core.module';
import { FleetModule } from './fleet/fleet.module';
import { HermesConfigModule } from './hermes-config/hermes-config.module';
import { InstallationModule } from './installation/installation.module';
import { McpModule } from './mcp/mcp.module';
import { SkillsModule } from './skills/skills.module';

@Module({
  imports: [
    CoreModule,
    InstallationModule,
    HermesConfigModule,
    SkillsModule,
    McpModule,
    CatalogModule,
    FleetModule,
  ],
})
export class AppModule {}
