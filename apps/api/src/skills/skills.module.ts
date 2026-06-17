import { Module } from '@nestjs/common';
import { HermesConfigModule } from '../hermes-config/hermes-config.module';
import { InstallationModule } from '../installation/installation.module';
import { SkillsController } from './skills.controller';
import { SkillsService } from './skills.service';

@Module({
  imports: [InstallationModule, HermesConfigModule],
  controllers: [SkillsController],
  providers: [SkillsService],
})
export class SkillsModule {}
