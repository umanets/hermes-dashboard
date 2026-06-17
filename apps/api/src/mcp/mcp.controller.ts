import { BadRequestException, Body, Controller, Get, Param, Patch } from '@nestjs/common';
import { z } from 'zod';
import { McpService } from './mcp.service';

const ToggleSchema = z.object({ enabled: z.boolean() });

@Controller('api/mcp-servers')
export class McpController {
  constructor(private readonly mcp: McpService) {}

  @Get()
  list() {
    return this.mcp.list();
  }

  @Get(':name')
  get(@Param('name') name: string) {
    return this.mcp.get(name);
  }

  @Patch(':name')
  toggleServer(@Param('name') name: string, @Body() body: unknown) {
    const parsed = ToggleSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.mcp.setServerEnabled(name, parsed.data.enabled);
  }

  @Patch(':name/tools/:tool')
  toggleTool(@Param('name') name: string, @Param('tool') tool: string, @Body() body: unknown) {
    const parsed = ToggleSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.mcp.setToolEnabled(name, tool, parsed.data.enabled);
  }
}
