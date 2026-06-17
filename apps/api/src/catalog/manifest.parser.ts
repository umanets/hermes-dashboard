import { parse } from 'yaml';
import { z } from 'zod';
import { HermesManifest } from './manifest.types';

const EnvVarSchema = z.object({
  name: z.string().min(1),
  prompt: z.string().default(''),
  default: z.string().optional(),
  required: z.boolean().default(false),
  secret: z.boolean().default(false),
  file: z.boolean().default(false),
});

const ManifestSchema = z
  .object({
    manifest_version: z.number(),
    name: z.string().min(1),
    description: z.string().default(''),
    source: z.string().optional(),
    transport: z.object({
      type: z.enum(['stdio', 'http']),
      command: z.string().optional(),
      args: z.array(z.string()).optional(),
      url: z.string().optional(),
    }),
    install: z
      .object({
        type: z.enum(['git', 'npm', 'uvx', 'local']),
        url: z.string().optional(),
        ref: z.string().optional(),
        bootstrap: z.array(z.string()).optional(),
        warmup: z.string().optional(),
      })
      .optional(),
    auth: z
      .object({
        type: z.enum(['api_key', 'oauth', 'none']),
        env: z.array(EnvVarSchema).default([]),
      })
      .default({ type: 'none', env: [] }),
    tools: z
      .object({ default_enabled: z.array(z.string()).default([]) })
      .default({ default_enabled: [] }),
    provides: z
      .object({
        server_env: z.record(z.string()).optional(),
        skills: z.array(z.object({ src: z.string(), dest: z.string() })).optional(),
        hooks: z
          .array(z.object({ src: z.string(), dest: z.string(), template: z.boolean().optional() }))
          .optional(),
        cron: z
          .array(
            z.object({
              id: z.string(),
              name: z.string().optional(),
              schedule: z.string(),
              no_agent: z.boolean().optional(),
              python: z.string().optional(),
              script: z.string(),
              workdir: z.string().optional(),
              env: z.record(z.string()).optional(),
            }),
          )
          .optional(),
      })
      .optional(),
    ingest: z.object({ script: z.string(), label: z.string().optional() }).optional(),
    post_install: z.string().optional(),
  })
  .superRefine((m, ctx) => {
    if (m.transport.type === 'stdio' && !m.transport.command) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'stdio transport requires transport.command', path: ['transport', 'command'] });
    }
    if (m.transport.type === 'http' && !m.transport.url) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'http transport requires transport.url', path: ['transport', 'url'] });
    }
  });

export interface ManifestParseResult {
  manifest: HermesManifest | null;
  valid: boolean;
  errors: string[];
}

export function parseManifest(content: string): ManifestParseResult {
  let raw: unknown;
  try {
    raw = parse(content);
  } catch (err) {
    return { manifest: null, valid: false, errors: [`Invalid YAML: ${(err as Error).message}`] };
  }
  const result = ManifestSchema.safeParse(raw);
  if (!result.success) {
    return {
      manifest: null,
      valid: false,
      errors: result.error.issues.map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`),
    };
  }
  const m = result.data;
  return {
    valid: true,
    errors: [],
    manifest: {
      manifestVersion: m.manifest_version,
      name: m.name,
      description: m.description,
      source: m.source,
      transport: m.transport,
      install: m.install,
      auth: m.auth,
      tools: m.tools,
      provides: m.provides,
      ingest: m.ingest,
      post_install: m.post_install,
    },
  };
}
