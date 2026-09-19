/** docx-preview 渲染（多页可滚动） */
export async function renderDocxPreview(buf: ArrayBuffer, host: HTMLElement): Promise<void> {
  const styleEl = document.createElement('div');
  styleEl.setAttribute('data-wjgl-docx-style', '1');
  document.head.appendChild(styleEl);
  host.innerHTML = '';

  try {
    const mod = await import('docx-preview');
    await mod.renderAsync(buf, host, styleEl, {
      inWrapper: true,
      ignoreFonts: true,
      breakPages: true,
      ignoreLastRenderedPageBreak: false,
      renderHeaders: true,
      renderFooters: true,
      renderFootnotes: true,
      renderEndnotes: true,
    });
    host.querySelectorAll('section.docx, .docx-wrapper, .docx').forEach((node) => {
      const el = node as HTMLElement;
      el.style.height = 'auto';
      el.style.minHeight = '0';
      el.style.maxHeight = 'none';
      el.style.overflow = 'visible';
    });
  } finally {
    styleEl.remove();
  }
}
