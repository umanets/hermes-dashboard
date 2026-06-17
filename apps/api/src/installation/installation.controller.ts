import { BadRequestException, Body, Controller, Delete, Get, Post } from '@nestjs/common';
import { z } from 'zod';
import { InstallationService } from './installation.service';

const ConnectSchema = z.object({ path: z.string().min(1) });

@Controller('api/installation')
export class InstallationController {
  constructor(private readonly installation: InstallationService) {}

  @Post('connect')
  async connect(@Body() body: unknown) {
    const parsed = ConnectSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.installation.connect(parsed.data.path);
  }

  @Get()
  current() {
    return this.installation.getActive();
  }

  @Delete()
  disconnect() {
    this.installation.disconnect();
    return { disconnected: true };
  }
}
