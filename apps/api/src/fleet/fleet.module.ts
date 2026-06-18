import { Module } from '@nestjs/common';
import { HermesConfigModule } from '../hermes-config/hermes-config.module';
import { InstallationModule } from '../installation/installation.module';
import { SkillLibraryModule } from '../skill-library/skill-library.module';
import { ClientRegistryService } from './client-registry.service';
import { FleetController } from './fleet.controller';
import { FleetService } from './fleet.service';

@Module({
  imports: [InstallationModule, HermesConfigModule, SkillLibraryModule],
  controllers: [FleetController],
  providers: [ClientRegistryService, FleetService],
})
export class FleetModule {}
