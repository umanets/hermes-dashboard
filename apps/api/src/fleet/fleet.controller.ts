import { BadRequestException, Body, Controller, Delete, Get, Param, Post, Query } from '@nestjs/common';
import { z } from 'zod';
import { FleetService } from './fleet.service';

const CreateSchema = z.object({ name: z.string().min(1) });

@Controller('api/fleet')
export class FleetController {
  constructor(private readonly fleet: FleetService) {}

  @Get('image')
  image() {
    return this.fleet.imageStatus();
  }

  @Post('image/build')
  build() {
    return this.fleet.startImageBuild();
  }

  @Get('image/build-jobs/:jobId')
  buildJob(@Param('jobId') jobId: string) {
    return this.fleet.getBuildJob(jobId);
  }

  // ---- shared Hindsight memory service ----
  @Get('memory')
  memory() {
    return this.fleet.memoryStatus();
  }

  @Post('memory/up')
  memoryUp() {
    return this.fleet.startMemory();
  }

  @Get('memory/jobs/:jobId')
  memoryJob(@Param('jobId') jobId: string) {
    return this.fleet.getMemoryJob(jobId);
  }

  @Post('memory/stop')
  memoryStop() {
    return this.fleet.memoryStop();
  }

  @Get('memory/logs')
  memoryLogs(@Query('tail') tail?: string) {
    return this.fleet.memoryLogs(tail ? Number(tail) : 200);
  }

  @Get('clients')
  clients() {
    return this.fleet.listClients();
  }

  @Post('clients')
  create(@Body() body: unknown) {
    const parsed = CreateSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.fleet.createClient(parsed.data.name);
  }

  @Post('clients/:id/start')
  start(@Param('id') id: string) {
    return this.fleet.start(id);
  }
  @Post('clients/:id/stop')
  stop(@Param('id') id: string) {
    return this.fleet.stop(id);
  }
  @Post('clients/:id/restart')
  restart(@Param('id') id: string) {
    return this.fleet.restart(id);
  }

  @Delete('clients/:id')
  remove(@Param('id') id: string, @Query('removeData') removeData?: string) {
    return this.fleet.remove(id, removeData === 'true' || removeData === '1');
  }

  @Get('clients/:id/logs')
  logs(@Param('id') id: string, @Query('tail') tail?: string) {
    return this.fleet.logs(id, tail ? Number(tail) : 200);
  }

  @Post('clients/:id/gateway/start')
  startGateway(@Param('id') id: string) {
    return this.fleet.startGateway(id);
  }

  @Post('clients/:id/gateway/stop')
  stopGateway(@Param('id') id: string) {
    return this.fleet.stopGateway(id);
  }

  @Get('clients/:id/gateway/status')
  gatewayStatus(@Param('id') id: string) {
    return this.fleet.gatewayStatus(id);
  }

  @Post('clients/:id/select')
  select(@Param('id') id: string) {
    return this.fleet.select(id);
  }

  // ---- per-client memory provider (Hindsight native) ----
  @Get('clients/:id/memory')
  memoryProvider(@Param('id') id: string) {
    return this.fleet.memoryProviderStatus(id);
  }

  @Post('clients/:id/memory/hindsight')
  enableHindsight(@Param('id') id: string) {
    return this.fleet.enableHindsightMemory(id);
  }

  @Post('clients/:id/memory/off')
  disableMemory(@Param('id') id: string) {
    return this.fleet.disableMemory(id);
  }
}
