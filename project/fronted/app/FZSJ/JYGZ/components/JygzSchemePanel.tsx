'use client';

/**
 * JYGZ 单文档方案区：左侧 Markdown 标题目录 + 预览（MarkdownRenderer）/ 编辑（contentEditable + 与 JYLLM 相同的格式工具栏与 HTML↔MD 转换）。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';
import { Typography, Collapse, Segmented, Button } from 'antd';
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import MathAwareMarkdown from '@/components/MathAwareMarkdown';
import SchemeFormatToolbar from '../../JYLLM/components/SchemeFormatToolbar';
import { schemeEditableHtmlToMarkdown, schemeMarkdownToEditableHtml } from '../../JYLLM/lib/schemeHtmlMarkdown';
import {
  extractMarkdownHeadingOutline,
  headingOutlineToTree,
  filterSchemeTocHeadings,
  type HeadingOutlineNode,
} from '../../JYLLM/lib/streamingMarkdownFix';

const { Text } = Typography;

const DEBOUNCE_MS = 1200;
const DOC_PREFIX = 'jygz-doc';

function TocScrollButton({ id, text, paddingLeft }: { id: string; text: string; paddingLeft: number }) {
  const display = text.length > 56 ? `${text.slice(0, 54)}…` : text;
  return (
    <button
      type="button"
      onClick={() => {
        document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }}
      style={{
        display: 'block',
        width: '100%',
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
  );
}

function TocNestedByLevel({ nodes, depth }: { nodes: HeadingOutlineNode[]; depth: number }) {
  const indent = 6 + depth * 8;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {nodes.map((node) =>
        node.children.length === 0 ? (
          <TocScrollButton key={node.id} id={node.id} text={node.text} paddingLeft={indent} />
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
                label: <TocScrollButton id={node.id} text={node.text} paddingLeft={indent} />,
                children: (
                  <div
                    style={{
                      marginLeft: 4,
                      paddingLeft: 6,
                      borderLeft: '1px solid #f0f0f0',
                    }}
                  >
                    <TocNestedByLevel nodes={node.children} depth={depth + 1} />
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

function EditableSingleMd({
  markdown,
  onChange,
  flushRef,
}: {
  markdown: string;
  onChange: (s: string) => void;
  flushRef: MutableRefObject<(() => void) | null>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    if (!ref.current) return;
    onChange(schemeEditableHtmlToMarkdown(ref.current.innerHTML));
  }, [onChange]);

  useEffect(() => {
    flushRef.current = flush;
    return () => {
      flushRef.current = null;
    };
  }, [flush, flushRef]);

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
        minHeight: 320,
        fontSize: 14,
        lineHeight: 1.8,
        color: '#333',
      }}
    />
  );
}

export interface JygzSchemePanelProps {
  /** 全文 Markdown（目录、编辑区同步源） */
  markdown: string;
  /** 预览区展示内容（可与 markdown 不同，如打字机阶段为前缀） */
  previewDisplayMd: string;
  view: 'preview' | 'edit';
  onViewChange: (view: 'preview' | 'edit') => void;
  onMarkdownChange: (md: string) => void;
  /** 与 JYLLM 插图上传一致；JYGZ 未接 task 时可不传 */
  taskId?: string | null;
}

export default function JygzSchemePanel({
  markdown,
  previewDisplayMd,
  view,
  onViewChange,
  onMarkdownChange,
  taskId = null,
}: JygzSchemePanelProps) {
  const [tocCollapsed, setTocCollapsed] = useState(false);
  const flushRef = useRef<(() => void) | null>(null);

  const tocTree = useMemo(() => {
    const raw = (markdown || '').replace(/\r\n/g, '\n');
    const heads = filterSchemeTocHeadings(extractMarkdownHeadingOutline(raw, DOC_PREFIX), 0);
    return headingOutlineToTree(heads);
  }, [markdown]);

  const previewHeadingIds = useMemo(
    () => extractMarkdownHeadingOutline((previewDisplayMd || '').replace(/\r\n/g, '\n'), DOC_PREFIX).map((h) => h.id),
    [previewDisplayMd],
  );

  const handleSegChange = (v: string) => {
    const next = v as 'preview' | 'edit';
    if (view === 'edit' && next === 'preview') {
      try {
        flushRef.current?.();
      } catch {
        /* ignore */
      }
    }
    onViewChange(next);
  };

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
            width: 220,
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
            <Text style={{ fontSize: 12, color: '#8c8c8c' }}>目录（至项目与指标）</Text>
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
          {tocTree.length === 0 ? (
            <div style={{ fontSize: 12, color: '#bfbfbf' }}>正文中使用 #～###### 标题后将显示层级</div>
          ) : (
            <TocNestedByLevel nodes={tocTree} depth={0} />
          )}
        </nav>
      )}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0', background: '#fafafa' }}>
          <Segmented
            value={view}
            onChange={(v) => handleSegChange(String(v))}
            options={[
              { label: '预览', value: 'preview' },
              { label: '编辑', value: 'edit' },
            ]}
          />
        </div>
        {view === 'edit' ? <SchemeFormatToolbar taskId={taskId} /> : null}
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px', minHeight: 0 }}>
          {view === 'preview' ? (
            <div className="jyllm-md-preview" style={{ fontSize: 14, lineHeight: 1.8, color: '#333' }}>
              <MathAwareMarkdown
                content={previewDisplayMd}
                docHeadingIds={previewHeadingIds}
                schemeSubHeadingsUniform
                allowHtml
              />
            </div>
          ) : (
            <EditableSingleMd markdown={markdown} onChange={onMarkdownChange} flushRef={flushRef} />
          )}
        </div>
      </div>
    </div>
  );
}
