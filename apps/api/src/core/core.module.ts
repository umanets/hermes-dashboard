import { Global, Module } from '@nestjs/common';
import { AtomicFileService } from './atomic-file.service';
import { BackupService } from './backup.service';
import { DockerService } from './docker.service';
import { EnvFileService } from './env-file.service';
import { FileLockService } from './file-lock.service';
import { PathService } from './path.service';
import { RedactionService } from './redaction.service';
import { ShellService } from './shell.service';
import { YamlDocService } from './yaml-doc.service';

/**
 * Global filesystem-safety layer. Every feature that touches a Hermes install's
 * files does so ONLY through these primitives.
 */
@Global()
@Module({
  providers: [
    PathService,
    RedactionService,
    AtomicFileService,
    BackupService,
    EnvFileService,
    FileLockService,
    YamlDocService,
    ShellService,
    DockerService,
  ],
  exports: [
    PathService,
    RedactionService,
    AtomicFileService,
    BackupService,
    EnvFileService,
    FileLockService,
    YamlDocService,
    ShellService,
    DockerService,
  ],
})
export class CoreModule {}
