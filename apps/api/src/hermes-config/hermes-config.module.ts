import { Module } from '@nestjs/common';
import { HermesConfigService } from './hermes-config.service';

@Module({
  providers: [HermesConfigService],
  exports: [HermesConfigService],
})
export class HermesConfigModule {}
