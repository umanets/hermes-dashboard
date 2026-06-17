import * as fs from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { AtomicFileService } from './atomic-file.service';
import { BackupService } from './backup.service';
import { EnvFileService } from './env-file.service';
import { FileLockService } from './file-lock.service';

describe('EnvFileService', () => {
  const svc = new EnvFileService(new FileLockService(), new BackupService(), new AtomicFileService());
  let dir: string;
  let envPath: string;

  beforeEach(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), 'env-test-'));
    envPath = path.join(dir, '.env');
  });
  afterEach(async () => {
    await fs.rm(dir, { recursive: true, force: true });
  });

  it('strips a BOM on read and preserves unrelated keys + comments on write', async () => {
    await fs.writeFile(envPath, '﻿# header\nEXISTING=keep\nTIDAL_COUNTRY=US\n', 'utf8');
    await svc.setMany(envPath, { TIDAL_CLIENT_SECRET: 'sek-ret', TIDAL_COUNTRY: 'UA' });

    const raw = await fs.readFile(envPath); // bytes, to assert no BOM
    expect(raw[0]).not.toBe(0xef);
    const text = raw.toString('utf8');
    expect(text).toContain('# header');
    expect(text).toContain('EXISTING=keep');
    expect(text).toContain('TIDAL_CLIENT_SECRET=sek-ret');
    expect(text).toContain('TIDAL_COUNTRY=UA'); // updated in place
    expect(text).not.toContain('TIDAL_COUNTRY=US');
  });

  it('writes Windows paths verbatim without quoting', async () => {
    await svc.setMany(envPath, { APPLE_PRIVATE_KEY_PATH: 'C:\\secure\\AuthKey_X.p8' });
    const text = await fs.readFile(envPath, 'utf8');
    expect(text).toContain('APPLE_PRIVATE_KEY_PATH=C:\\secure\\AuthKey_X.p8');
  });

  it('rejects multiline values', async () => {
    await expect(svc.setMany(envPath, { BAD: 'line1\nline2' })).rejects.toThrow(/newline/);
  });

  it('reports presence via read()', async () => {
    await svc.setMany(envPath, { A: '1' });
    const map = await svc.read(envPath);
    expect(map.has('A')).toBe(true);
    expect(map.has('NOPE')).toBe(false);
  });
});
