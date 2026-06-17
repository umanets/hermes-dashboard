import { Module } from '@nestjs/common';
import { InstallationController } from './installation.controller';
import { InstallationService } from './installation.service';

@Module({
  controllers: [InstallationController],
  providers: [InstallationService],
  exports: [InstallationService],
})
export class InstallationModule {}
