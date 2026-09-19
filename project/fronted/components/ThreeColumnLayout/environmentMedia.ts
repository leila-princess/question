/** 将 &lt;table&gt; 等实体编码还原为 HTML（来源 API / 图谱属性常见） */
export function decodeHtmlEntities(raw: string): string {
  const s = (raw || "").trim();
  if (!s || !/&(?:lt|gt|amp|quot|#39|#\d+);/i.test(s)) return s;
  if (typeof document !== "undefined") {
    const ta = document.createElement("textarea");
    ta.innerHTML = s;
    return ta.value;
  }
  return s
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)));
}

/** 环境属性字段：换行还原 + 实体解码 + 媒体 URL 改写 */
export function normalizeEnvironmentFieldRaw(raw: string): string {
  let s = unescapeEnvironmentNewlines(String(raw || ""));
  s = decodeHtmlEntities(s);
  return rewriteEnvironmentMediaUrls(s).trim();
}

/** 将字面量 \\n、\\r\\n 及真实换行统一为 \n */
export function unescapeEnvironmentNewlines(raw: string): string {
  if (!raw) return "";
  return raw
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/\\n/g, "\n");
}

/** MinIO 环境图片代理（走 Next.js → Flask JQCX /minio-image） */
export function minioImageProxyUrl(objectPath: string): string {
  const path = (objectPath || '')
    .trim()
    .replace(/^\/+/, '')
    .replace(/^faxm\//, '');
  if (!path) return '';
  return `/api/GLCX/JQCX/minio-image?path=${encodeURIComponent(path)}`;
}

/** 将 presigned / 裸路径 / 历史错误代理格式 转为可长期访问的代理 URL */
export function rewriteEnvironmentMediaUrls(content: string): string {
  if (!content) return '';

  let s = content;

  // 历史错误格式：/api/GLCX/JQCX?path=/minio-image&path=环境%2F...
  s = s.replace(
    /\/api\/GLCX\/JQCX\?path=\/minio-image&path=([^"'\s)<>]+)/gi,
    (_, enc: string) => `/api/GLCX/JQCX/minio-image?path=${enc}`,
  );

  // presigned 或直连 MinIO → 代理
  s = s.replace(
    /https?:\/\/[^/\s"'<>]+(?::\d+)?\/faxm\/([^?"'\s<>]+)(\?[^"'\s<>]*)?/gi,
    (_, objPath: string) => minioImageProxyUrl(decodeURIComponent(objPath)),
  );

  // markdown 图片
  s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_m, alt: string, url: string) => {
    const faxm = url.match(/\/faxm\/([^?]+)/);
    if (faxm) {
      return `![${alt || '环境图片'}](${minioImageProxyUrl(decodeURIComponent(faxm[1]))})`;
    }
    if (/^环境\//.test(url.trim())) {
      return `![${alt || '环境图片'}](${minioImageProxyUrl(url.trim())})`;
    }
    return `![${alt || '环境图片'}](${url})`;
  });

  // HTML img src
  s = s.replace(
    /<img\b([^>]*)\bsrc=(['"])([^'"]+)\2([^>]*)>/gi,
    (_m, before: string, q: string, src: string, after: string) => {
      let next = src;
      const faxm = src.match(/\/faxm\/([^?]+)/);
      if (faxm) {
        next = minioImageProxyUrl(decodeURIComponent(faxm[1]));
      } else if (/^环境\//.test(src.trim())) {
        next = minioImageProxyUrl(src.trim());
      }
      return `<img${before}src=${q}${next}${q}${after}>`;
    },
  );

  // 仅有「存储路径 / 路径」无 img 时补 img
  s = s.replace(
    /(?:^|\n)\s*(?:环境图片\s*)?(?:存储路径|路径)[：:]\s*\n?\s*(环境\/[^\s\n<]+)\s*(?=\n|$)/gi,
    (_m, path: string) => {
      const p = path.trim();
      const url = minioImageProxyUrl(p);
      return `\n<div><img src="${url}" alt="环境图片" style="max-width:100%;max-height:360px;object-fit:contain;" /></div>\n<div style="margin:4px 0 8px;color:#8c8c8c;font-size:12px;">路径：${p}</div>\n`;
    },
  );

  // 单独的「环境图片」占位行（下一段是路径）— 去掉空行占位
  s = s.replace(/(?:^|\n)\s*环境图片\s*(?=\n\s*(?:存储路径|路径)[：:])/gi, '\n');

  return s;
}

/** 为 HTML img 补充展示样式与下载链接（MinIO 代理 URL 同源可下载） */
export function enrichHtmlImagesWithDownloadLinks(html: string): string {
  if (!html || !/<img/i.test(html)) return html;
  return html.replace(
    /<img\b([^>]*)\bsrc=(['"])([^'"]+)\2([^>]*)\/?>/gi,
    (_m, before: string, q: string, src: string, after: string) => {
      const imgStyle =
        'max-width:100%;max-height:360px;object-fit:contain;display:block;border-radius:6px';
      const mergedBefore = /style=/i.test(before)
        ? before
        : `${before} style="${imgStyle}"`;
      return `<figure class="env-media-figure" style="margin:12px 0"><img${mergedBefore}src=${q}${src}${q}${after} /><p style="margin:6px 0 0"><a href="${src}" download target="_blank" rel="noopener noreferrer" style="font-size:13px;color:#1677ff">下载图片</a></p></figure>`;
    },
  );
}
