'use client';

/**
 * 右侧方案正文：全文章节目录（含小节标题）+ 生成中流式规范化 Markdown / 完成后预览与富文本编辑切换。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Typography, Collapse, Segmented, Button, Dropdown, Space } from 'antd';
import { BulbOutlined, SearchOutlined, MenuFoldOutlined, MenuUnfoldOutlined, EditOutlined, MoreOutlined } from '@ant-design/icons';
import MathAwareMarkdown from '@/components/MathAwareMarkdown';
import type { ChapterModule, RegionRewriteSpec } from '../types/flow';
import { schemeEditableHtmlToMarkdown, schemeMarkdownToEditableHtml } from '../lib/schemeHtmlMarkdown';
import { findMethodIdsInChapter, getSelectionTextFromDocument } from '../lib/regionRewrite';
import {
  extractMarkdownHeadingOutline,
  headingOutlineToTree,
  filterSchemeTocHeadings,
  normalizeStreamingMarkdown,
  type HeadingOutlineNode,
} from '../lib/streamingMarkdownFix';
import {
  stripJyllmMethodSentinels,
  stripDuplicateLeadingChapterHeading,
  clampStreamingChapter1Subsections,
} from '../lib/chapterStreamDisplay';
import { useTypewriterReveal } from '../hooks/useTypewriterReveal';
import SchemeFormatToolbar from './SchemeFormatToolbar';

const { Text } = Typography;

const DEBOUNCE_MS = 1200;

function sortedChapters(modules: ChapterModule[]) {
  return [...modules].sort((a, b) => a.index - b.index);
}

function TocScrollButton({
  id,
  text,
  paddingLeft,
  onRewrite,
}: {
  id: string;
  text: string;
  paddingLeft: number;
  onRewrite?: () => void;
}) {
  const display = text.length > 56 ? `${text.slice(0, 54)}…` : text;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 2, width: '100%' }}>
      <button
        type="button"
        onClick={() => {
          document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }}
        style={{
          display: 'block',
          flex: 1,
          textAlign: 'left',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          fontSize: 12,
          color: '#262626',
          padding: '4px 4px 4px 6px',
          borderRadius: 4,
          lineHeight: 1.35,
          paddingLeft,
        }}
        onMouseEnter={(ev) => {
          (ev.currentTarget as HTMLButtonElement).style.background = '#f0f5ff';
        }}
        onMouseLeave={(ev) => {
          (ev.currentTarget as HTMLButtonElement).style.background = 'transparent';
        }}
      >
        <span style={{ wordBreak: 'break-all' }}>{display}</span>
      </button>
      {onRewrite ? (
        <Button
          type="text"
          size="small"
          icon={<EditOutlined />}
          title="重写本节"
          onClick={(e) => {
            e.stopPropagation();
            onRewrite();
          }}
        />
      ) : null}
    </div>
  );
}

/** 章内目录：按标题层级嵌套折叠，直至叶子小节 */
function TocNestedByLevel({
  nodes,
  depth,
  chapterIndex,
  onRewriteHeading,
}: {
  nodes: HeadingOutlineNode[];
  depth: number;
  chapterIndex: number;
  onRewriteHeading?: (headingId: string, headingText: string) => void;
}) {
  const indent = 6 + depth * 8;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {nodes.map((node) =>
        node.children.length === 0 ? (
          <TocScrollButton
            key={node.id}
            id={node.id}
            text={node.text}
            paddingLeft={indent}
            onRewrite={onRewriteHeading ? () => onRewriteHeading(node.id, node.text) : undefined}
          />
        ) : (
          <Collapse
            key={node.id}
            ghost
            bordered={false}
            size="small"
            expandIconPosition="end"
            defaultActiveKey={[node.id]}
            styles={{
              header: { padding: '2px 0', minHeight: 26, background: 'transparent' },
              body: { padding: '2px 0 6px 0', background: 'transparent' },
            }}
            items={[
              {
                key: node.id,
                collapsible: 'icon',
                label: (
                  <TocScrollButton
                    id={node.id}
                    text={node.text}
                    paddingLeft={indent}
                    onRewrite={onRewriteHeading ? () => onRewriteHeading(node.id, node.text) : undefined}
                  />
                ),
                children: (
                  <div
                    style={{
                      marginLeft: 4,
                      paddingLeft: 6,
                      borderLeft: '1px solid #f0f0f0',
                    }}
                  >
                    <TocNestedByLevel
                      nodes={node.children}
                      depth={depth + 1}
                      chapterIndex={chapterIndex}
                      onRewriteHeading={onRewriteHeading}
                    />
                  </div>
                ),
              },
            ]}
          />
        ),
      )}
    </div>
  );
}

/** 生成态：后端已写入完整 content，本组件用打字机从「缓冲区」追赶展示，形成顺序写作观感 */
function GeneratingMarkdownPreview({
  chapterTitle,
  raw,
  chapterIndex,
  typewriterActive,
  docHeadingIdPrefix,
}: {
  chapterTitle: string;
  raw: string;
  chapterIndex: number;
  typewriterActive: boolean;
  docHeadingIdPrefix: string;
}) {
  let r = raw || '';
  if (chapterIndex === 1) {
    r = clampStreamingChapter1Subsections(r);
  }
  const normalized = normalizeStreamingMarkdown(r);
  const processed = stripJyllmMethodSentinels(stripDuplicateLeadingChapterHeading(chapterTitle, normalized));
  const revealed = useTypewriterReveal(processed, typewriterActive, { maxStep: 22, intervalMs: 20 });
  const previewMd = typewriterActive ? revealed : processed;
  const headingIds = extractMarkdownHeadingOutline(previewMd, docHeadingIdPrefix).map((h) => h.id);
  return (
    <div className="jyllm-md-preview" style={{ fontSize: 14, lineHeight: 1.8, color: '#333' }}>
      <MathAwareMarkdown
        content={previewMd}
        docHeadingIds={headingIds}
        schemeSubHeadingsUniform
        allowHtml
      />
    </div>
  );
}

function EditableChapterBody({
  chapterIndex,
  markdown,
  onMarkdownChange,
  registerFlush,
}: {
  chapterIndex: number;
  markdown: string;
  onMarkdownChange: (idx: number, md: string) => void;
  /** 供父组件在切换到「预览」前同步 innerHTML→Markdown（避免卸载快于 blur） */
  registerFlush?: (chapterIndex: number, flush: () => string) => () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback((): string => {
    if (!ref.current) return markdown;
    const md = schemeEditableHtmlToMarkdown(ref.current.innerHTML);
    onMarkdownChange(chapterIndex, md);
    return md;
  }, [chapterIndex, markdown, onMarkdownChange]);

  useEffect(() => {
    if (!registerFlush) return undefined;
    return registerFlush(chapterIndex, flush);
  }, [chapterIndex, flush, registerFlush]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (document.activeElement === el) return;
    el.innerHTML = schemeMarkdownToEditableHtml(markdown);
  }, [markdown]);

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  return (
    <div
      ref={ref}
      className="jyllm-scheme-editable jyllm-md-preview"
      contentEditable
      suppressContentEditableWarning
      onBlur={flush}
      onInput={() => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(flush, DEBOUNCE_MS);
      }}
      style={{
        outline: 'none',
        minHeight: 80,
        fontSize: 14,
        lineHeight: 1.8,
        color: '#333',
      }}
    />
  );
}

interface SchemeDocumentPanelProps {
  modules: ChapterModule[];
  /** 生成中为 true：只读 Markdown + 流式版式修补 */
  isGenerating: boolean;
  /** 当前正在「打字展示」的章节序号；与 isGenerating 配合形成缓冲区追赶效果 */
  currentGeneratingChapter: number | null;
  onChapterContentChange: (chapterIndex: number, markdown: string) => void;
  onRequestRegionRewrite?: (spec: RegionRewriteSpec, regionPreview: string) => void;
  /** 有 taskId 时插图会上传到 MinIO 并写入持久 URL */
  taskId?: string | null;
}

function SelectionRewriteBar({
  visible,
  onRewrite,
}: {
  visible: boolean;
  onRewrite: () => void;
}) {
  if (!visible) return null;
  return (
    <div
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 5,
        marginBottom: 8,
        padding: '6px 10px',
        background: '#e6f4ff',
        border: '1px solid #91caff',
        borderRadius: 6,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}
    >
      <Text style={{ fontSize: 12 }}>已选中文字，可针对该区域重写（含表格）</Text>
      <Button type="primary" size="small" icon={<EditOutlined />} onClick={onRewrite}>
        重写选中
      </Button>
    </div>
  );
}

export default function SchemeDocumentPanel({
  modules,
  isGenerating,
  currentGeneratingChapter,
  onChapterContentChange,
  onRequestRegionRewrite,
  taskId = null,
}: SchemeDocumentPanelProps) {
  const list = sortedChapters(modules);
  const [panelView, setPanelView] = useState<'preview' | 'edit'>('preview');
  const [selectionVisible, setSelectionVisible] = useState(false);
  const [activeEditChapter, setActiveEditChapter] = useState<number | null>(null);
  /** 生成区/预览区左侧目录可收起，腾出正文宽度 */
  const [tocCollapsed, setTocCollapsed] = useState(false);
  const flushByChapterRef = useRef<Map<number, () => string>>(new Map());

  const registerChapterFlush = useCallback((chapterIndex: number, flush: () => string) => {
    flushByChapterRef.current.set(chapterIndex, flush);
    return () => {
      flushByChapterRef.current.delete(chapterIndex);
    };
  }, []);

  useEffect(() => {
    if (isGenerating) setPanelView('preview');
  }, [isGenerating]);

  const requestRewrite = useCallback(
    (spec: RegionRewriteSpec, preview: string) => {
      onRequestRegionRewrite?.(spec, preview);
    },
    [onRequestRegionRewrite],
  );

  const getChapterRaw = useCallback(
    (chapterIndex: number) => list.find((m) => m.index === chapterIndex)?.content || '',
    [list],
  );

  const getChapterTitle = useCallback(
    (chapterIndex: number) => list.find((m) => m.index === chapterIndex)?.title || `第 ${chapterIndex} 章`,
    [list],
  );

  const handleRewriteChapter = useCallback(
    (chapterIndex: number, title: string) => {
      const content = getChapterRaw(chapterIndex);
      requestRewrite(
        {
          region_type: 'chapter',
          chapter_index: chapterIndex,
          chapter_content: content,
          chapter_title: title,
          label: title,
        },
        content.slice(0, 2000),
      );
    },
    [getChapterRaw, requestRewrite],
  );

  const handleRewriteHeading = useCallback(
    (chapterIndex: number, headingId: string, headingText: string) => {
      const content = getChapterRaw(chapterIndex);
      const chapterTitle = getChapterTitle(chapterIndex);
      requestRewrite(
        {
          region_type: 'heading',
          chapter_index: chapterIndex,
          chapter_content: content,
          chapter_title: chapterTitle,
          heading_id: headingId,
          heading_text: headingText,
          label: headingText,
        },
        `（标题节）${headingText}`,
      );
    },
    [getChapterRaw, getChapterTitle, requestRewrite],
  );

  const handleRewriteMethod = useCallback(
    (chapterIndex: number, methodId: string) => {
      const content = getChapterRaw(chapterIndex);
      const chapterTitle = getChapterTitle(chapterIndex);
      requestRewrite(
        {
          region_type: 'method',
          chapter_index: chapterIndex,
          chapter_content: content,
          chapter_title: chapterTitle,
          method_id: methodId,
          label: methodId.slice(0, 12),
        },
        `（方法块）${methodId}`,
      );
    },
    [getChapterRaw, getChapterTitle, requestRewrite],
  );

  const handleRewriteSelection = useCallback(
    (chapterIndex: number) => {
      const selected = getSelectionTextFromDocument();
      if (!selected.trim()) {
        return;
      }
      const flushed = flushByChapterRef.current.get(chapterIndex)?.();
      const content =
        typeof flushed === 'string' && flushed.trim() ? flushed : getChapterRaw(chapterIndex);
      const chapterTitle = getChapterTitle(chapterIndex);
      requestRewrite(
        {
          region_type: 'selection',
          chapter_index: chapterIndex,
          chapter_content: content,
          chapter_title: chapterTitle,
          selected_text: selected,
          label: selected.slice(0, 24),
        },
        selected.slice(0, 2000),
      );
    },
    [getChapterRaw, getChapterTitle, requestRewrite],
  );

  useEffect(() => {
    if (panelView !== 'edit') {
      setSelectionVisible(false);
      return undefined;
    }
    const onSel = () => {
      const t = getSelectionTextFromDocument();
      setSelectionVisible(Boolean(t.trim()));
    };
    document.addEventListener('selectionchange', onSel);
    return () => document.removeEventListener('selectionchange', onSel);
  }, [panelView]);

  const tocEntries = useMemo(() => {
    return list.flatMap((m) => {
      const futureChapter =
        isGenerating && currentGeneratingChapter != null && m.index > currentGeneratingChapter;
      let raw = futureChapter ? '' : m.content || '';
      if (isGenerating && m.index === 1) {
        raw = clampStreamingChapter1Subsections(raw);
      }
      const md = isGenerating ? normalizeStreamingMarkdown(raw) : raw;
      const proc = stripJyllmMethodSentinels(stripDuplicateLeadingChapterHeading(m.title, md));
      const heads = filterSchemeTocHeadings(
        extractMarkdownHeadingOutline(proc, `jyllm-ch-${m.index}`),
        m.index,
      );
      return heads.map((h) => ({
        ...h,
        chapterIndex: m.index,
        chapterTitle: m.title || `第 ${m.index} 章`,
      }));
    });
  }, [list, isGenerating, currentGeneratingChapter]);

  /** 目录按大章分组；章内为标题树，多级均可折叠至叶子 */
  const tocChapterGroups = useMemo(
    () =>
      list.map((m) => {
        const entries = tocEntries.filter((e) => e.chapterIndex === m.index);
        const tree = headingOutlineToTree(entries.map(({ id, level, text }) => ({ id, level, text })));
        return {
          chapterIndex: m.index,
          chapterTitle: m.title || `第 ${m.index} 章`,
          entries,
          tree,
        };
      }),
    [list, tocEntries],
  );

  if (!list.length) {
    return (
      <div style={{ color: '#8c8c8c', padding: 24, textAlign: 'center' }}>
        {isGenerating ? '正在生成中…' : '暂无章节内容'}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0, flexDirection: 'row' }}>
      {tocCollapsed ? (
        <div
          style={{
            width: 40,
            flexShrink: 0,
            borderRight: '1px solid #f0f0f0',
            background: '#fcfcfc',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            paddingTop: 8,
            gap: 4,
          }}
        >
          <Button
            type="text"
            size="small"
            icon={<MenuUnfoldOutlined />}
            onClick={() => setTocCollapsed(false)}
            title="展开目录"
            aria-label="展开目录"
            style={{ color: '#595959' }}
          />
          <span
            style={{
              writingMode: 'vertical-rl',
              fontSize: 11,
              color: '#8c8c8c',
              letterSpacing: 2,
              userSelect: 'none',
            }}
          >
            目录
          </span>
        </div>
      ) : (
        <nav
          style={{
            width: 248,
            flexShrink: 0,
            borderRight: '1px solid #f0f0f0',
            overflowY: 'auto',
            padding: '8px 10px',
            background: '#fcfcfc',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 6,
              marginBottom: 8,
            }}
          >
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>目录（至项目与指标）</span>
            <Button
              type="text"
              size="small"
              icon={<MenuFoldOutlined style={{ fontSize: 13 }} />}
              onClick={() => setTocCollapsed(true)}
              title="收起目录"
              aria-label="收起目录"
              style={{ color: '#8c8c8c', flexShrink: 0 }}
            />
          </div>
          {tocChapterGroups.every((g) => g.entries.length === 0) ? (
            <div style={{ fontSize: 12, color: '#bfbfbf' }}>生成标题后将显示层级…</div>
          ) : (
            <Collapse
              ghost
              bordered={false}
              size="small"
              expandIconPosition="end"
              defaultActiveKey={tocChapterGroups.map((g) => String(g.chapterIndex))}
              style={{ background: 'transparent' }}
              styles={{
                header: {
                  padding: '4px 2px 4px 0',
                  minHeight: 32,
                  alignItems: 'center',
                  background: 'transparent',
                },
                body: { padding: '2px 0 6px 0', background: 'transparent' },
              }}
              items={tocChapterGroups.map((g) => ({
                key: String(g.chapterIndex),
                label: (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#434343' }}>{g.chapterTitle}</span>
                    {onRequestRegionRewrite && !isGenerating ? (
                      <Button
                        type="link"
                        size="small"
                        style={{ fontSize: 11, padding: 0, height: 'auto' }}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRewriteChapter(g.chapterIndex, g.chapterTitle);
                        }}
                      >
                        重写本章
                      </Button>
                    ) : null}
                  </div>
                ),
                children:
                  g.entries.length === 0 ? (
                    <div style={{ fontSize: 11, color: '#bfbfbf', paddingLeft: 4 }}>暂无小节标题</div>
                  ) : (
                    <TocNestedByLevel
                      nodes={g.tree}
                      depth={0}
                      chapterIndex={g.chapterIndex}
                      onRewriteHeading={
                        onRequestRegionRewrite
                          ? (hid, htext) => handleRewriteHeading(g.chapterIndex, hid, htext)
                          : undefined
                      }
                    />
                  ),
              }))}
            />
          )}
        </nav>
      )}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {!isGenerating ? (
          <div style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0', background: '#fafafa' }}>
            <Segmented
              value={panelView}
              onChange={(v) => {
                const next = v as 'preview' | 'edit';
                if (panelView === 'edit' && next === 'preview') {
                  flushByChapterRef.current.forEach((fn) => {
                    try {
                      fn();
                    } catch {
                      /* ignore */
                    }
                  });
                }
                setPanelView(next);
              }}
              options={[
                { label: '预览', value: 'preview' },
                { label: '编辑', value: 'edit' },
              ]}
            />
          </div>
        ) : null}
        {!isGenerating && panelView === 'edit' ? (
          <>
            <SchemeFormatToolbar taskId={taskId} />
            <SelectionRewriteBar
              visible={selectionVisible}
              onRewrite={() => {
                const ch = activeEditChapter ?? list[0]?.index;
                if (ch != null) handleRewriteSelection(ch);
              }}
            />
          </>
        ) : null}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
          {list.map((m) => {
            const futureChapter =
              isGenerating && currentGeneratingChapter != null && m.index > currentGeneratingChapter;
            const raw = m.content || '';
            const rawForBody = futureChapter ? '' : raw;
            const twActive = Boolean(isGenerating && currentGeneratingChapter === m.index);
            let rawForToc = rawForBody;
            if (isGenerating && m.index === 1) {
              rawForToc = clampStreamingChapter1Subsections(rawForToc);
            }
            const mdForToc = isGenerating ? normalizeStreamingMarkdown(rawForToc) : rawForToc;
            const procForToc = stripJyllmMethodSentinels(stripDuplicateLeadingChapterHeading(m.title, mdForToc));
            const headingIds = extractMarkdownHeadingOutline(procForToc, `jyllm-ch-${m.index}`).map((h) => h.id);

            return (
              <section
                key={m.index}
                id={`jyllm-ch-${m.index}`}
                style={{ marginBottom: 28, scrollMarginTop: 12 }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    fontSize: 17,
                    fontWeight: 600,
                    marginBottom: 12,
                    color: '#1f1f1f',
                    paddingBottom: 8,
                    borderBottom: '1px solid #e5e7eb',
                  }}
                >
                  <span>{m.title}</span>
                  {onRequestRegionRewrite && !isGenerating ? (
                    <Space size={4}>
                      <Button
                        size="small"
                        icon={<EditOutlined />}
                        onClick={() => handleRewriteChapter(m.index, m.title)}
                      >
                        重写本章
                      </Button>
                      {findMethodIdsInChapter(raw).length > 0 ? (
                        <Dropdown
                          menu={{
                            items: findMethodIdsInChapter(raw).map((mid) => ({
                              key: mid,
                              label: `方法 ${mid.slice(0, 10)}…`,
                              onClick: () => handleRewriteMethod(m.index, mid),
                            })),
                          }}
                        >
                          <Button size="small" icon={<MoreOutlined />}>
                            重写方法
                          </Button>
                        </Dropdown>
                      ) : null}
                    </Space>
                  ) : null}
                </div>

                {m.thinking && m.thinking.trim() ? (
                  <Collapse
                    size="small"
                    ghost
                    defaultActiveKey={[]}
                    style={{ marginBottom: 12, background: 'rgba(0,0,0,0.02)', borderRadius: 6 }}
                    items={[
                      {
                        key: 'thinking',
                        label: (
                          <Text style={{ fontSize: 12, color: '#8c8c8c' }}>
                            <BulbOutlined style={{ marginRight: 4 }} />
                            思考过程
                          </Text>
                        ),
                        children: (
                          <div
                            style={{
                              fontSize: 12,
                              color: '#8c8c8c',
                              lineHeight: 1.8,
                              padding: '0 8px',
                              whiteSpace: 'pre-wrap',
                            }}
                          >
                            {m.thinking}
                          </div>
                        ),
                      },
                    ]}
                  />
                ) : null}

                {isGenerating || panelView === 'preview' ? (
                  isGenerating ? (
                    <GeneratingMarkdownPreview
                      chapterTitle={m.title}
                      raw={rawForBody}
                      chapterIndex={m.index}
                      typewriterActive={twActive}
                      docHeadingIdPrefix={`jyllm-ch-${m.index}`}
                    />
                  ) : (
                    <div className="jyllm-md-preview" style={{ fontSize: 14, lineHeight: 1.8, color: '#333' }}>
                      <MathAwareMarkdown
                        content={procForToc}
                        docHeadingIds={headingIds}
                        schemeSubHeadingsUniform
                        allowHtml
                      />
                    </div>
                  )
                ) : (
                  <div
                    onFocus={() => setActiveEditChapter(m.index)}
                    onMouseUp={() => setActiveEditChapter(m.index)}
                  >
                    <EditableChapterBody
                      chapterIndex={m.index}
                      markdown={stripJyllmMethodSentinels(stripDuplicateLeadingChapterHeading(m.title, raw))}
                      onMarkdownChange={onChapterContentChange}
                      registerFlush={registerChapterFlush}
                    />
                  </div>
                )}

                {(m.keywords || m.searchContext) && (
                  <div
                    style={{
                      marginTop: 16,
                      padding: 12,
                      background: '#fafafa',
                      borderRadius: 8,
                      borderTop: '1px solid #e5e7eb',
                    }}
                  >
                    {m.keywords ? (
                      <div style={{ marginBottom: 8, fontSize: 12, color: '#595959' }}>
                        关键词：{m.keywords}
                      </div>
                    ) : null}
                    {m.searchContext ? (
                      <Collapse
                        size="small"
                        ghost
                        style={{ background: 'transparent' }}
                        items={[
                          {
                            key: 'search',
                            label: (
                              <Text style={{ fontSize: 12, color: '#8c8c8c' }}>
                                <SearchOutlined style={{ marginRight: 4 }} />
                                检索结果
                              </Text>
                            ),
                            children: (
                              <div
                                style={{
                                  fontSize: 12,
                                  color: '#595959',
                                  lineHeight: 1.6,
                                  padding: '0 8px',
                                  maxHeight: 200,
                                  overflow: 'auto',
                                }}
                              >
                                <MathAwareMarkdown content={m.searchContext} allowHtml />
                              </div>
                            ),
                          },
                        ]}
                      />
                    ) : null}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
