import { Module } from '@nestjs/common';
import { InstallationModule } from '../installation/installation.module';
import { SkillLibraryController } from './skill-library.controller';
import { SkillLibraryService } from './skill-library.service';

@Module({
  imports: [InstallationModule],
  controllers: [SkillLibraryController],
  providers: [SkillLibraryService],
  exports: [SkillLibraryService],
})
export class SkillLibraryModule {}
