export interface SkillFrontmatter {
  name: string;
  description: string;
  version?: string;
  license?: string;
  author?: string;
  platforms?: ('macos' | 'linux' | 'windows')[];
  prerequisites?: { env_vars?: string[]; commands?: string[] };
  metadata?: { hermes?: { tags?: string[] } };
  [k: string]: unknown;
}

export interface Skill {
  /** identity used in config.yaml skills.disabled (frontmatter name, else dir name) */
  name: string;
  category: string;
  path: string;
  /** DERIVED: !skills.disabled.includes(name) */
  enabled: boolean;
  frontmatter: SkillFrontmatter | null;
  frontmatterValid: boolean;
  frontmatterErrors: string[];
}
