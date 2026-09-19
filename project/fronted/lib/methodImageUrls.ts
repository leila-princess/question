/**
 * 从方法节点 files / SYNR HTML 汇总 MinIO 对象路径，供方案预览插图代理使用。
 */
export function collectMethodImageUrls(
  synrHtml: string,
  files?: Array<Record<string, unknown> | null | undefined> | null,
  existing?: string | string[] | null,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];

  const push = (p: string) => {
    const norm = p.replace(/\\/g, '/').trim().replace(/^\/+/, '');
    if (norm && !seen.has(norm)) {
      seen.add(norm);
      out.push(norm);
    }
  };

  if (Array.isArray(existing)) {
    for (const x of existing) push(String(x));
  } else if (typeof existing === 'string' && existing.trim()) {
    const s = existing.trim();
    if (s.startsWith('[')) {
      try {
        const j = JSON.parse(s) as unknown;
        if (Array.isArray(j)) {
          for (const x of j) push(String(x));
        }
      } catch {
        push(s);
      }
    } else {
      for (const p of s.split(/[,;\n|]/)) push(p);
    }
  }

  if (files?.length) {
    for (const f of files) {
      if (!f) continue;
      for (const key of ['minio_id', 'minIo_id', 'MinIo_id', 'minio_path', 'path']) {
        const val = f[key];
        if (val) push(String(val));
      }
    }
  }

  if (synrHtml) {
    const re = /<img\b[^>]*\bsrc=["']([^"']+)["']/gi;
    let m: RegExpExecArray | null;
    while ((m = re.exec(synrHtml)) !== null) {
      const src = m[1];
      if (src.includes('path=')) {
        const raw = src.split('path=')[1].split('&')[0];
        try {
          const path = decodeURIComponent(raw);
          if (path.startsWith('试验方法/')) push(path);
        } catch {
          /* ignore */
        }
      } else if (src.startsWith('试验方法/')) {
        push(src);
      }
    }
  }

  return out;
}
