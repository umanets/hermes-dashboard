import { Injectable } from '@nestjs/common';
import { Document, parseDocument } from 'yaml';

type Path = (string | number)[];

/**
 * Full-document YAML round-trip. Parsing into a `yaml` Document retains the
 * entire tree (and comments); we mutate only the specific nodes we manage via
 * setIn/deleteIn and re-serialize the SAME document. We never rebuild a fresh
 * object — that is exactly how unmanaged sections get silently dropped.
 *
 * Reads derive from the whole document via {@link toJS}; writes go through
 * {@link set}/{@link delete} so untouched nodes keep their original formatting.
 */
@Injectable()
export class YamlDocService {
  parse(content: string): Document {
    return parseDocument(content);
  }

  stringify(doc: Document): string {
    return doc.toString({ lineWidth: 0 });
  }

  set(doc: Document, path: Path, value: unknown): void {
    doc.setIn(path, value);
  }

  delete(doc: Document, path: Path): void {
    doc.deleteIn(path);
  }

  has(doc: Document, path: Path): boolean {
    return doc.hasIn(path);
  }

  /** The whole document as plain JS — for READ-only derivations. */
  toJS<T = Record<string, unknown>>(doc: Document): T {
    return (doc.toJS() ?? {}) as T;
  }
}
