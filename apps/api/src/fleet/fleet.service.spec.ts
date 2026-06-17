import * as fs from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { AtomicFileService } from '../core/atomic-file.service';
import { PathService } from '../core/path.service';
import { ShellService } from '../core/shell.service';
import { InstallationService } from '../installation/installation.service';
import { ClientRegistryService } from './client-registry.service';
import { FleetService } from './fleet.service';

class FakeDocker {
  calls: [string, string?][] = [];
  netCalls: string[] = [];
  runs: any[] = [];
  runArgs: any = null;
  imageThere = true;
  statusVal: string | null = 'running';
  async available() { return true; }
  async imageExists() { return this.imageThere; }
  async run(opts: any) { this.runArgs = opts; this.runs.push(opts); this.calls.push(['run', opts.name]); return { ok: true, id: 'cid' }; }
  async status(name: string) { this.calls.push(['status', name]); return this.statusVal; }
  async start(n: string) { this.calls.push(['start', n]); return { ok: true }; }
  async stop(n: string) { this.calls.push(['stop', n]); return { ok: true }; }
  async restart(n: string) { this.calls.push(['restart', n]); return { ok: true }; }
  async rm(n: string) { this.calls.push(['rm', n]); return { ok: true }; }
  async logs() { return 'logz'; }
  execOut = { code: 0, stdout: '', stderr: '', ok: true };
  async exec(name: string, argv: string[]) { this.calls.push(['exec', `${name} ${argv.join(' ')}`]); return this.execOut; }
  async execDetached(name: string, argv: string[]) { this.calls.push(['execDetached', `${name} ${argv.join(' ')}`]); return this.execOut; }
  async ensureNetwork(n: string) { this.netCalls.push(`ensure:${n}`); return { ok: true }; }
  async connectNetwork(net: string, c: string) { this.netCalls.push(`connect:${net}:${c}`); return { ok: true }; }
  async pull(tag: string, onLine: (l: string) => void) { onLine(`pulled ${tag}`); return true; }
}

describe('FleetService (stubbed docker)', () => {
  let root: string;
  let fleet: FleetService;
  let docker: FakeDocker;
  let installation: InstallationService;
  const prev = process.env.CLIENTS_ROOT;

  beforeAll(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), 'fleet-'));
    process.env.CLIENTS_ROOT = root;
    const paths = new PathService();
    const registry = new ClientRegistryService(new AtomicFileService(), paths);
    installation = new InstallationService(paths);
    docker = new FakeDocker();
    fleet = new FleetService(registry, docker as any, installation, paths, new ShellService(), { mutate: async () => ({ backupPath: null }) } as any);
  });
  afterAll(async () => {
    process.env.CLIENTS_ROOT = prev;
    await fs.rm(root, { recursive: true, force: true });
  });

  it('creates an agent: slug, docker run args, registry, setup command', async () => {
    const { client, setupCommand } = await fleet.createClient('Acme Alice');
    expect(client.id).toBe('acme-alice');
    expect(client.containerName).toBe('hermes-acme-alice');
    expect(client.hostDir.endsWith('acme-alice/.hermes')).toBe(true);
    expect(setupCommand).toBe('docker exec -it hermes-acme-alice hermes setup');
    // docker run got the right volume + env + joined the fleet network
    expect(docker.runArgs.volumes[0].container).toBe('/opt/data');
    expect(docker.runArgs.volumes[0].host).toContain('acme-alice');
    expect(docker.runArgs.env.HERMES_UID).toBeDefined();
    expect(docker.runArgs.restart).toBe(true);
    expect(docker.runArgs.network).toBe('hermes-net');
    expect(docker.netCalls).toEqual(expect.arrayContaining(['ensure:hermes-net']));
  });

  it('lists agents with live status + setupDone=false (no config yet)', async () => {
    const list = await fleet.listClients();
    expect(list.length).toBe(1);
    expect(list[0].status).toBe('running');
    expect(list[0].setupDone).toBe(false);
  });

  it('start/stop/restart target the container name', async () => {
    await fleet.start('acme-alice');
    await fleet.stop('acme-alice');
    await fleet.restart('acme-alice');
    expect(docker.calls).toEqual(expect.arrayContaining([
      ['start', 'hermes-acme-alice'], ['stop', 'hermes-acme-alice'], ['restart', 'hermes-acme-alice'],
    ]));
  });

  it('restart relaunches the gateway only if it was running', async () => {
    docker.execOut = { code: 0, stdout: 'gateway is running', stderr: '', ok: true };
    const r1: any = await fleet.restart('acme-alice');
    expect(r1.gatewayRelaunched).toBe(true);
    expect(docker.calls).toEqual(expect.arrayContaining([['exec', 'hermes-acme-alice hermes gateway start']]));

    docker.execOut = { code: 0, stdout: 'gateway is stopped', stderr: '', ok: true };
    const r2: any = await fleet.restart('acme-alice');
    expect(r2.gatewayRelaunched).toBe(false);
    docker.execOut = { code: 0, stdout: '', stderr: '', ok: true };
  });

  it('select() scopes the existing dashboard to the client volume (invalid until setup)', async () => {
    const inst = await fleet.select('acme-alice');
    expect(inst.valid).toBe(false); // no config.yaml yet
    expect(installation.getActiveOrThrow().rootPath).toContain('acme-alice');
  });

  it('rejects create when the base image is missing', async () => {
    docker.imageThere = false;
    await expect(fleet.createClient('beta')).rejects.toThrow(/image/i);
    docker.imageThere = true;
  });

  it('remove deletes the container and registry entry', async () => {
    await fleet.remove('acme-alice', false);
    expect(docker.calls).toEqual(expect.arrayContaining([['rm', 'hermes-acme-alice']]));
    expect(await fleet.listClients()).toHaveLength(0);
  });
});

describe('FleetService — shared Hindsight memory (stubbed docker)', () => {
  let root: string;
  let fleet: FleetService;
  let docker: FakeDocker;
  const prevRoot = process.env.CLIENTS_ROOT;
  const prevKey = process.env.OPENROUTER_API_KEY;

  beforeAll(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), 'fleet-mem-'));
    process.env.CLIENTS_ROOT = root;
    const paths = new PathService();
    const registry = new ClientRegistryService(new AtomicFileService(), paths);
    docker = new FakeDocker();
    fleet = new FleetService(registry, docker as any, new InstallationService(paths), paths, new ShellService(), { mutate: async () => ({ backupPath: null }) } as any);
  });
  afterAll(async () => {
    process.env.CLIENTS_ROOT = prevRoot;
    if (prevKey === undefined) delete process.env.OPENROUTER_API_KEY;
    else process.env.OPENROUTER_API_KEY = prevKey;
    await fs.rm(root, { recursive: true, force: true });
  });

  const settle = (jobId: string) =>
    new Promise<void>((resolve) => {
      const tick = () => (fleet.getMemoryJob(jobId).status === 'running' ? setTimeout(tick, 15) : resolve());
      tick();
    });

  it('refuses to start without OPENROUTER_API_KEY', () => {
    delete process.env.OPENROUTER_API_KEY;
    expect(() => fleet.startMemory()).toThrow(/OPENROUTER_API_KEY/);
  });

  it('pulls + runs hindsight on hermes-net with openrouter env, and attaches existing clients', async () => {
    process.env.OPENROUTER_API_KEY = 'sk-or-test';
    await fleet.createClient('bob'); // a client to be attached
    docker.statusVal = null; // hindsight not yet created → run path
    const { jobId } = fleet.startMemory();
    await settle(jobId);

    expect(fleet.getMemoryJob(jobId).status).toBe('completed');
    const hs = docker.runs.find((r) => r.name === 'hindsight');
    expect(hs).toBeTruthy();
    expect(hs.network).toBe('hermes-net');
    expect(hs.volumes[0].container).toBe('/home/hindsight/.pg0');
    expect(hs.env.HINDSIGHT_API_LLM_PROVIDER).toBe('openrouter');
    expect(hs.env.HINDSIGHT_API_LLM_API_KEY).toBe('sk-or-test');
    expect(hs.env.HINDSIGHT_API_LLM_MODEL).toBe('openai/gpt-4o-mini');
    expect(hs.env.HINDSIGHT_API_LLM_SEND_BANK_AS_USER).toBe('true');
    expect(docker.netCalls).toEqual(expect.arrayContaining(['ensure:hermes-net', 'connect:hermes-net:hermes-bob']));
  });
});
