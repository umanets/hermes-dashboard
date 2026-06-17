export type McpTransport = 'stdio' | 'http' | 'unknown';
export type ToolFilterMode = 'all' | 'include' | 'exclude';

export interface McpTool {
  name: string;
  enabled: boolean;
}

export interface McpServer {
  name: string;
  transport: McpTransport;
  /** DERIVED: enabled !== false */
  enabled: boolean;
  command?: string;
  args?: string[];
  url?: string;
  toolFilterMode: ToolFilterMode;
  /** Known tool states. In 'all' mode this is empty (universe unknown from config alone). */
  tools: McpTool[];
  warnings: string[];
}
