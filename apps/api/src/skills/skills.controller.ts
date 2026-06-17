import { BadRequestException, Body, Controller, Get, Param, Patch } from '@nestjs/common';
import { z } from 'zod';
import { SkillsService } from './skills.service';

const ToggleSchema = z.object({ enabled: z.boolean() });
const BulkSchema = z.object({ enabled: z.boolean(), category: z.string().optional() });

@Controller('api/skills')
export class SkillsController {
  constructor(private readonly skills: SkillsService) {}

  @Get()
  list() {
    return this.skills.list();
  }

  @Get(':name/preview')
  preview(@Param('name') name: string) {
    return this.skills.getPreview(name);
  }

  /** Bulk toggle: all skills, or one category. Body: { enabled, category? }. */
  @Patch()
  async bulk(@Body() body: unknown) {
    const parsed = BulkSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.skills.setBulkEnabled(parsed.data.enabled, parsed.data.category);
  }

  @Patch(':name')
  async toggle(@Param('name') name: string, @Body() body: unknown) {
    const parsed = ToggleSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.skills.setEnabled(name, parsed.data.enabled);
  }
}
