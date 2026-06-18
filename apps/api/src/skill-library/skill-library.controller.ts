import { BadRequestException, Body, Controller, Get, Param, Post } from '@nestjs/common';
import { z } from 'zod';
import { SkillLibraryService } from './skill-library.service';

const InstallSchema = z.object({ category: z.string().min(1), name: z.string().min(1) });

@Controller('api/skill-library')
export class SkillLibraryController {
  constructor(private readonly library: SkillLibraryService) {}

  @Get(':collection')
  list(@Param('collection') collection: string) {
    return this.library.list(collection);
  }

  @Get(':collection/:category/:name/preview')
  preview(@Param('collection') collection: string, @Param('category') category: string, @Param('name') name: string) {
    return this.library.preview(collection, category, name);
  }

  /** Copy a library skill into the currently-selected install's skills dir. */
  @Post(':collection/install')
  install(@Param('collection') collection: string, @Body() body: unknown) {
    const parsed = InstallSchema.safeParse(body);
    if (!parsed.success) throw new BadRequestException(parsed.error.issues);
    return this.library.installToActive(collection, parsed.data.category, parsed.data.name);
  }
}
