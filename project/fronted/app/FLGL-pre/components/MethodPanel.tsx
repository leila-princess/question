'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Upload, Button, message, Drawer, Space, Typography, Spin, Modal, Progress, Form, Input, Tabs, Select, Table, InputNumber, Alert, Row, Col } from 'antd';
import { UploadOutlined, PlusOutlined, DeleteOutlined, EditOutlined, CheckOutlined } from '@ant-design/icons';
import GenericNodePanel from './GenericNodePanel';
import RelationModal from './RelationModal';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import { extractMethod, importMethod, fetchNodes, checkMethodNameDuplicate, type GraphNode, type NodeType } from '../api';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

const RELATION_TARGET_TYPES: { value: NodeType; label: string }[] = [
  { value: 'EquipmentType', label: '设备类型' },
  { value: 'Equipment', label: '设备' },
  { value: 'Environment', label: '环境' },
  { value: 'TestProject', label: '项目' },
  { value: 'Indicator', label: '指标' },
  { value: 'File', label: '文件' },
];

const METHOD_REL_TYPES = [
  { value: 'CON' as const, label: 'CON' },
  { value: 'HAS' as const, label: 'HAS' },
  { value: 'BELONG_TO' as const, label: 'BELONG_TO' },
];

const METHOD_STATUS_OPTIONS = [
  { label: '启用', value: 'active' },
  { label: '停用', value: 'inactive' },
];

const REL_TYPE_SET = new Set(['CON', 'HAS', 'BELONG_TO'] as const);

type RelationTargetType = (typeof RELATION_TARGET_TYPES)[number]['value'];
type RelTypeKind = (typeof METHOD_REL_TYPES)[number]['value'];

function newRelRowKey() {
  return `rel-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/** 与 backend method_block_markdown 对齐：结构化块 → Markdown，避免整段 JSON 展示 */
function stripHtmlToPlain(html: string): string {
  if (!html || !String(html).trim()) return '';
  let s = String(html);
  s = s.replace(/<\s*br\s*\/?>/gi, '\n');
  s = s.replace(/<\/(tr|h[1-6]|p|div|li|th|td)>/gi, ' | ');
  s = s.replace(/<[^>]+>/g, ' ');
  s = s.replace(/[ \t]+/g, ' ');
  s = s.replace(/\s*\|\s*/g, ' | ');
  s = s.replace(/\n\s*\n+/g, '\n\n');
  return s.trim();
}

function textBlockToMarkdown(block: unknown): string {
  if (block == null) return '';
  if (typeof block === 'string') return block.trim();
  if (typeof block !== 'object' || Array.isArray(block)) return '';
  const o = block as { text?: string; tables_html?: string[]; formulas_latex?: string[] };
  const text = String(o.text ?? '').trim();
  const tables = Array.isArray(o.tables_html) ? o.tables_html : [];
  const formulas = Array.isArray(o.formulas_latex) ? o.formulas_latex : [];
  const parts: string[] = [];
  if (text) parts.push(text);
  formulas.forEach((raw, i) => {
    const t = String(raw ?? '').trim();
    if (t) {
      parts.push(`**公式 ${i + 1}**（LaTeX）\n\n\`\`\`latex\n${t}\n\`\`\``);
    }
  });
  tables.forEach((raw, i) => {
    const h = String(raw ?? '').trim();
    if (!h) return;
    const plain = stripHtmlToPlain(h);
    if (plain) {
      parts.push(`**表格 ${i + 1}**（自 HTML 提取为文本）\n\n${plain}`);
    } else {
      const cut = h.length > 12000 ? `${h.slice(0, 12000)}…` : h;
      parts.push(`**表格 ${i + 1}**（无可用文本，原 HTML 见下）\n\n\`\`\`html\n${cut}\n\`\`\``);
    }
  });
  return parts.join('\n\n').trim();
}

interface MethodRelationRow {
  key: string;
  targetType: RelationTargetType;
  targetId: string;
  relType: RelTypeKind;
  score?: number | null;
}

function rowsFromRecommended(
  recs?: Array<{
    target_type?: string;
    target_id?: string;
    rel_type?: string;
    score?: number;
  }>,
): MethodRelationRow[] {
  if (!recs?.length) return [];
  const allow = new Set<string>(['EquipmentType', 'Equipment', 'Environment', 'TestProject', 'Indicator', 'File']);
  return recs.map((r, i) => {
    const tt = (r.target_type && allow.has(r.target_type) ? r.target_type : 'TestProject') as RelationTargetType;
    const rt = (r.rel_type && REL_TYPE_SET.has(r.rel_type as RelTypeKind) ? r.rel_type : 'CON') as RelTypeKind;
    const sc = r.score;
    const n = sc != null && sc !== ('' as unknown) ? Number(sc) : null;
    return {
      key: newRelRowKey() + i,
      targetType: tt,
      targetId: String(r.target_id || ''),
      relType: rt,
      score: n != null && !Number.isNaN(n) ? n : null,
    };
  });
}

function buildRelationsPayload(rows: MethodRelationRow[]) {
  const out: { target_id: string; rel_type: string; score?: number }[] = [];
  for (const row of rows) {
    const tid = (row.targetId || '').trim();
    if (!tid) continue;
    if (!REL_TYPE_SET.has(row.relType)) continue;
    if (row.score != null && (typeof row.score === 'number' ? Number.isNaN(row.score) : Number.isNaN(Number(row.score)))) {
      continue;
    }
    const o: { target_id: string; rel_type: string; score?: number } = { target_id: tid, rel_type: row.relType };
    if (row.score != null && row.score !== undefined) {
      const s = typeof row.score === 'number' ? row.score : Number(row.score);
      if (Number.isNaN(s)) continue;
      o.score = s;
    }
    out.push(o);
  }
  return out;
}

interface RecommendedNode {
  node_type: string;
  node_id: string;
  node_name: string;
  score: number;
}

interface MethodResult {
  name: string;
  type: string;
  /** 方法类别：总则 / 分则，与数据库属性 Class 对应；提取结果可能从 type 回退 */
  class?: string;
  label: string;
  description: string;
  sy: string;
  sytj: string;
  sygc: string;
  jgpd: string;
  source_name: string;
  source_id?: string;
  recommended_nodes?: RecommendedNode[];
  /** 默认推荐仅含 TestProject + Indicator 各前若干条；表格中可增选六类 */
  recommended_relations?: Array<{
    target_type: string;
    target_id: string;
    node_name?: string;
    rel_type: string;
    score?: number;
  }>;
  name_duplicate?: { duplicate: boolean; existing?: GraphNode | null };
}

interface ExtractMethodResponse {
  success?: boolean;
  data?: {
    methods?: MethodResult[];
    methods_count?: number;
    recommended_nodes?: RecommendedNode[];
  } & Partial<MethodResult>;
  error?: string;
}

type MethodClassKind = '总则' | '分则';

function inferMethodClass(m: { type?: string; class?: string }): MethodClassKind {
  const c = (m.class || '').trim();
  if (c === '总则' || c === '分则') return c;
  const t = (m.type || '').trim();
  if (t === '总则' || t === '分则') return t;
  return '分则';
}

interface EditableMethod {
  name: string;
  /** 图属性 Type，来自提取，表单不单独编辑 */
  type: string;
  methodClass: MethodClassKind;
  status: string;
  label: string;
  description: string;
  sy: string;
  sytj: string;
  sygc: string;
  jgpd: string;
  source_name: string;
  source_id?: string;
}

type Mdk = 'sytj' | 'sygc' | 'jgpd';

interface MethodEditorState {
  key: string;
  method: EditableMethod;
  savedNode?: GraphNode;
  relationRows: MethodRelationRow[];
  nameDuplicate: { duplicate: boolean; existing?: GraphNode | null };
  /** 仅当与库中重名时需要：replace=覆盖；skip=不保存；null=未选 */
  duplicateChoice: 'replace' | 'skip' | null;
  replaceNodeId?: string;
  showRenameInput: boolean;
  renameDraft: string;
  mdFieldEdit: Record<Mdk, boolean>;
}

function displayMethodSavedUser(editor: MethodEditorState): string {
  const n = editor.savedNode as GraphNode | undefined;
  if (!n) return '—';
  if (n.user_name != null && String(n.user_name).trim() !== '') return String(n.user_name);
  if (n.user_id != null && String(n.user_id).trim() !== '') return String(n.user_id);
  return '—';
}

function displayMethodSavedTime(editor: MethodEditorState): string {
  const n = editor.savedNode as GraphNode | undefined;
  if (n == null || n.create_time == null) return '—';
  const s = String(n.create_time).trim();
  return s || '—';
}

export default function MethodPanel({
  allNodes, selectedCategory, categoryNodes, onRefresh, methodCategoryMap,
}: {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
  methodCategoryMap?: Record<string, string[]>;
}) {
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [methodEditors, setMethodEditors] = useState<MethodEditorState[]>([]);
  const [activeMethodTab, setActiveMethodTab] = useState('method-0');
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  const [uploading, setUploading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [redirecting, setRedirecting] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [progressVisible, setProgressVisible] = useState(false);
  const [progressPercent, setProgressPercent] = useState(0);
  const [progressText, setProgressText] = useState('');
  const [fileNodes, setFileNodes] = useState<GraphNode[]>([]);
  const [nodeOptionsByType, setNodeOptionsByType] = useState<Partial<Record<NodeType, GraphNode[]>>>({});
  const nameDebounceRef = useRef<Record<string, number | null>>({});

  const importBlocked = useMemo(() => {
    const ed = methodEditors.find((e) => e.key === activeMethodTab);
    if (!ed) return true;
    if (ed.duplicateChoice === 'skip') return true;
    if (ed.nameDuplicate.duplicate && ed.duplicateChoice == null) return true;
    return false;
  }, [methodEditors, activeMethodTab]);

  useEffect(() => {
    if (!drawerVisible) return;
    (async () => {
      const types: NodeType[] = [
        'EquipmentType',
        'Equipment',
        'Environment',
        'TestProject',
        'Indicator',
        'File',
      ];
      try {
        const results = await Promise.all(types.map((t) => fetchNodes(t).catch(() => [])));
        const map: Partial<Record<NodeType, GraphNode[]>> = {};
        types.forEach((t, i) => {
          map[t] = (Array.isArray(results[i]) ? results[i] : []) as GraphNode[];
        });
        setNodeOptionsByType(map);
        setFileNodes(map.File || []);
      } catch {
        /* 下拉可选，失败忽略 */
      }
    })();
  }, [refreshKey, drawerVisible]);

  const fileSelectOptions = useMemo(
    () =>
      fileNodes.map((f) => ({
        value: f.id,
        label: (f.name || f.label || f.id) as string,
      })),
    [fileNodes],
  );

  const updateFileSource = (tabKey: string, fileId: string | null) => {
    const f = fileId ? fileNodes.find((n) => n.id === fileId) : undefined;
    setMethodEditors((prev) =>
      prev.map((editor) =>
        editor.key === tabKey
          ? {
              ...editor,
              method: {
                ...editor.method,
                source_id: fileId || '',
                source_name: f ? String(f.name || f.label || '') : '',
              },
            }
          : editor
      )
    );
  };

  const updateRelationRow = (tabKey: string, rowKey: string, patch: Partial<MethodRelationRow>) => {
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        return {
          ...ed,
          relationRows: ed.relationRows.map((r) => {
            if (r.key !== rowKey) return r;
            const next: MethodRelationRow = { ...r, ...patch };
            if (Object.prototype.hasOwnProperty.call(patch, 'targetType') && patch.targetType !== r.targetType) {
              next.targetId = '';
            }
            return next;
          }),
        };
      }),
    );
  };

  const addRelationRow = (tabKey: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        return {
          ...ed,
          relationRows: [
            ...ed.relationRows,
            {
              key: newRelRowKey(),
              targetType: 'TestProject',
              targetId: '',
              relType: 'CON',
              score: null,
            },
          ],
        };
      }),
    );
  };

  const removeRelationRow = (tabKey: string, rowKey: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) => (ed.key === tabKey ? { ...ed, relationRows: ed.relationRows.filter((r) => r.key !== rowKey) } : ed)),
    );
  };

  useEffect(() => {
    if (!uploading || !progressVisible) return;

    const stages = [
      { percent: 8, text: '文件已上传，准备开始解析...' },
      { percent: 18, text: '正在识别文档内容，请稍候...' },
      { percent: 30, text: '正在提取章节与结构信息...' },
      { percent: 45, text: '正在分析试验方法内容...' },
      { percent: 60, text: '正在整理试验条件与试验过程...' },
      { percent: 75, text: '正在匹配关联节点...' },
      { percent: 85, text: '正在生成最终结果...' },
    ];

    let index = 0;
    setProgressPercent(stages[0].percent);
    setProgressText(stages[0].text);

    const timer = window.setInterval(() => {
      index += 1;
      if (index >= stages.length) {
        window.clearInterval(timer);
        return;
      }
      setProgressPercent(stages[index].percent);
      setProgressText(stages[index].text);
    }, 8000);

    return () => window.clearInterval(timer);
  }, [uploading, progressVisible]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    setProgressVisible(true);
    setProgressPercent(5);
    setProgressText('文件已上传，准备开始解析...');
    try {
      const result = await extractMethod(file) as ExtractMethodResponse;
      console.log('method-extract result:', result);

      if (!result) {
        setProgressVisible(false);
        message.error('后端未返回结果');
        return false;
      }

      if (result.success && result.data) {
        setProgressPercent(100);
        setProgressText('解析完成，正在打开结果界面...');
        const rawMethods = Array.isArray(result.data.methods) && result.data.methods.length
          ? result.data.methods
          : [{
              name: result.data.name || '',
              type: result.data.type || '',
              label: result.data.label || '',
              description: result.data.description || '',
              sy: result.data.sy || '',
              sytj: result.data.sytj || '',
              sygc: result.data.sygc || '',
              jgpd: result.data.jgpd || '',
              source_name: result.data.source_name || '',
              recommended_relations: (result.data as MethodResult & { recommended_relations?: MethodResult['recommended_relations'] }).recommended_relations,
              name_duplicate: (result.data as MethodResult & { name_duplicate?: MethodResult['name_duplicate'] }).name_duplicate,
            }];
        const editors: MethodEditorState[] = rawMethods.map((method, methodIndex) => {
          const nd = method.name_duplicate ?? { duplicate: false, existing: null as GraphNode | null | undefined };
          return {
            key: `method-${methodIndex}`,
            method: {
              name: method.name || '',
              type: method.type || '',
              methodClass: inferMethodClass(method),
              status: 'active',
              label: method.label || '',
              description: method.description || '',
              sy: method.sy || '',
              sytj: normalizeContent(method.sytj),
              sygc: normalizeContent(method.sygc),
              jgpd: normalizeContent(method.jgpd),
              source_name: method.source_name || '',
              source_id: method.source_id,
            },
            relationRows: rowsFromRecommended(method.recommended_relations),
            nameDuplicate: { duplicate: !!nd.duplicate, existing: nd.existing ?? null },
            duplicateChoice: null,
            replaceNodeId: undefined,
            showRenameInput: false,
            renameDraft: method.name || '',
            mdFieldEdit: { sytj: false, sygc: false, jgpd: false },
          };
        });
        setMethodEditors(editors);
        setActiveMethodTab(editors[0]?.key || 'method-0');
        window.setTimeout(() => {
          setProgressVisible(false);
          setDrawerVisible(true);
          message.success(`方法提取完成${result.data?.methods_count ? `（共 ${result.data.methods_count} 个方法）` : ''}`);
        }, 500);
      } else {
        setProgressVisible(false);
        message.error(result.error || '提取失败');
      }
    } catch (e: any) {
      setProgressVisible(false);
      console.error('method-extract failed:', e);
      message.error(e?.message || '提取请求失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const updateEditableField = (tabKey: string, field: keyof EditableMethod, value: string) => {
    setMethodEditors((prev) =>
      prev.map((editor) =>
        editor.key === tabKey ? { ...editor, method: { ...editor.method, [field]: value } } : editor,
      ),
    );
  };

  const applyNameDuplicateFromServer = useCallback((tabKey: string, name: string) => {
    if (!name.trim()) {
      setMethodEditors((prev) =>
        prev.map((ed) =>
          ed.key === tabKey
            ? { ...ed, nameDuplicate: { duplicate: false, existing: null }, duplicateChoice: null, replaceNodeId: undefined }
            : ed,
        ),
      );
      return;
    }
    void checkMethodNameDuplicate(name).then((res) => {
      if (!res?.success || res.data == null) return;
      setMethodEditors((prev) =>
        prev.map((ed) => {
          if (ed.key !== tabKey) return ed;
          if (ed.method.name?.trim() !== name.trim()) return ed;
          const dup = res.data!.duplicate;
          return {
            ...ed,
            nameDuplicate: { duplicate: dup, existing: res.data!.existing ?? null },
            duplicateChoice: dup ? ed.duplicateChoice : null,
            replaceNodeId: dup ? ed.replaceNodeId : undefined,
          };
        }),
      );
    });
  }, []);

  const onMethodNameChange = (tabKey: string, value: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) =>
        ed.key === tabKey
          ? {
              ...ed,
              method: { ...ed.method, name: value },
              duplicateChoice: null,
              replaceNodeId: undefined,
              showRenameInput: false,
              renameDraft: value,
            }
          : ed,
      ),
    );
    const tprev = nameDebounceRef.current[tabKey];
    if (tprev) window.clearTimeout(tprev);
    nameDebounceRef.current[tabKey] = window.setTimeout(() => {
      applyNameDuplicateFromServer(tabKey, value.trim());
    }, 450);
  };

  const openRenameForEditor = (tabKey: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) =>
        ed.key === tabKey
          ? { ...ed, showRenameInput: true, renameDraft: (ed.method.name || '').trim() }
          : ed,
      ),
    );
  };

  const applyRenameDraft = (tabKey: string) => {
    const ed = methodEditors.find((e) => e.key === tabKey);
    if (!ed) return;
    const next = (ed.renameDraft || '').trim();
    if (!next) {
      message.warning('请输入新名称');
      return;
    }
    setMethodEditors((prev) =>
      prev.map((e) =>
        e.key === tabKey
          ? { ...e, method: { ...e.method, name: next }, showRenameInput: false, duplicateChoice: null, replaceNodeId: undefined }
          : e,
      ),
    );
    void checkMethodNameDuplicate(next).then((res) => {
      if (!res?.success || res.data == null) return;
      setMethodEditors((prev) =>
        prev.map((e) => {
          if (e.key !== tabKey) return e;
          return {
            ...e,
            nameDuplicate: { duplicate: res.data!.duplicate, existing: res.data!.existing ?? null },
            duplicateChoice: res.data!.duplicate ? e.duplicateChoice : null,
            replaceNodeId: res.data!.duplicate ? e.replaceNodeId : undefined,
          };
        }),
      );
      if (!res.data!.duplicate) {
        message.success('新名称在数据库中无重复，可正常保存');
      } else {
        message.warning('该名称仍与库中方法重复，请重命名、替换或跳过');
      }
    });
  };

  const setDuplicateAction = (tabKey: string, action: 'replace' | 'skip' | 'clear', replaceId?: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        if (action === 'replace') {
          return { ...ed, duplicateChoice: 'replace', replaceNodeId: replaceId || String(ed.nameDuplicate.existing?.id || '') };
        }
        if (action === 'skip') {
          return { ...ed, duplicateChoice: 'skip' };
        }
        return { ...ed, duplicateChoice: null, replaceNodeId: undefined, showRenameInput: false, renameDraft: ed.method.name || '' };
      }),
    );
  };

  const setRenameDraftValue = (tabKey: string, v: string) => {
    setMethodEditors((prev) => prev.map((ed) => (ed.key === tabKey ? { ...ed, renameDraft: v } : ed)));
  };

  const toggleMdField = (tabKey: string, field: Mdk) => {
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        return { ...ed, mdFieldEdit: { ...ed.mdFieldEdit, [field]: !ed.mdFieldEdit[field] } };
      }),
    );
  };

  const handleOpenRelationModal = (editor: MethodEditorState) => {
    if (!editor.savedNode?.id) {
      message.warning('请先保存当前方法，再管理关联');
      return;
    }
    setRelationModal({
      open: true,
      node: editor.savedNode,
    });
  };

  const handleImport = async () => {
    const currentEditor = methodEditors.find(editor => editor.key === activeMethodTab);
    if (!currentEditor) return false;
    if (currentEditor.duplicateChoice === 'skip') {
      message.warning('本方法已选择「跳过」，不会保存到数据库');
      return false;
    }
    if (currentEditor.nameDuplicate.duplicate && currentEditor.duplicateChoice == null) {
      message.warning('名称与库中记录重复，请在上方选择「替换」「跳过」或使用「重命名」后保存');
      return false;
    }
    setImporting(true);
    try {
      const nodeData = {
        Name: currentEditor.method.name,
        Type: currentEditor.method.type || '分则',
        Class: currentEditor.method.methodClass,
        Label: currentEditor.method.label,
        Description: currentEditor.method.description,
        Sy: currentEditor.method.sy,
        Sytj: currentEditor.method.sytj,
        Sygc: currentEditor.method.sygc,
        Jgpd: currentEditor.method.jgpd,
        Source_name: currentEditor.method.source_name,
        Source_id: currentEditor.method.source_id || '',
        Status: (currentEditor.method.status || 'active').trim() || 'active',
      };
      const relations = buildRelationsPayload(currentEditor.relationRows || []);
      const relationDetails = (currentEditor.relationRows || []).map((r) => ({
        target_type: r.targetType,
        target_id: r.targetId,
        rel_type: r.relType,
        score: r.score,
      }));
      const importBody: Record<string, unknown> = {
        method: nodeData,
        relations,
        relation_details: relationDetails,
      };
      if (currentEditor.duplicateChoice === 'replace' && currentEditor.replaceNodeId) {
        importBody.import_mode = 'replace';
        importBody.replace_node_id = currentEditor.replaceNodeId;
      }
      const result = await importMethod(importBody);
      if (!result?.success) {
        if (result?.data?.duplicate) {
          message.warning(`该方法已存在：${result.data.existing?.name || result.data.existing?.id || ''}`);
        } else {
          message.error(result?.error || '导入失败');
        }
        return false;
      }

      const savedNode = result?.data?.node;
      setMethodEditors((prev) =>
        prev.map((editor) => {
          if (editor.key !== activeMethodTab) return editor;
          if (!savedNode) {
            return { ...editor, savedNode };
          }
          const n = savedNode as GraphNode;
          const cls = n.class;
          const nextClass: MethodClassKind =
            cls === '总则' || cls === '分则' ? cls : editor.method.methodClass;
          return {
            ...editor,
            savedNode,
            method: {
              ...editor.method,
              status: n.status != null && String(n.status).trim() !== '' ? String(n.status) : editor.method.status,
              methodClass: nextClass,
            },
          };
        }),
      );
      const successName = currentEditor.method.name?.trim();
      message.success(successName ? `方法“${successName}”导入成功` : '方法导入成功');
      setRefreshKey(k => k + 1);
      onRefresh();
      return true;
    } catch (e: any) {
      console.error('method-import failed:', e);
      message.error(e?.message || '导入失败');
      return false;
    } finally {
      setImporting(false);
    }
  };

  const handleImportAndUploadFile = async () => {
    const ok = await handleImport();
    if (ok) {
      setRedirecting(true);
      message.loading({ content: '正在跳转至文件管理界面...', key: 'redirect-file-page', duration: 0 });
      window.setTimeout(() => {
        window.location.href = '/ZSGL/WJGL';
      }, 300);
    }
  };


  const normalizeContent = (content: any): string => {
    if (content == null) return '';
    if (typeof content === 'string') return content;
    if (typeof content === 'object' && !Array.isArray(content) && content !== null && 'text' in content) {
      return textBlockToMarkdown(content);
    }
    if (Array.isArray(content)) {
      return content.map(item => {
        if (typeof item === 'string') return item;
        try {
          return JSON.stringify(item, null, 2);
        } catch {
          return String(item);
        }
      }).join('\n');
    }
    if (typeof content === 'object') {
      try {
        return JSON.stringify(content, null, 2);
      } catch {
        return String(content);
      }
    }
    return String(content);
  };

  const extraButtons = (
    <Upload beforeUpload={handleUpload} showUploadList={false} accept=".pdf,.doc,.docx,.txt">
      <Button icon={<UploadOutlined />} loading={uploading}>上传方法文件</Button>
    </Upload>
  );

  return (
    <>
      <GenericNodePanel
        nodeType="TestMethod"
        title="方法管理"
        allNodes={allNodes}
        selectedCategory={selectedCategory}
        categoryNodes={categoryNodes}
        onRefresh={onRefresh}
        extraButtons={extraButtons}
        refreshKey={refreshKey}
        methodCategoryMap={methodCategoryMap}
      />

      <Modal
        title="正在解析方法文件"
        open={progressVisible}
        footer={null}
        closable={false}
        maskClosable={false}
        centered
      >
        <div style={{ padding: '8px 0' }}>
          <Progress percent={progressPercent} status={uploading ? 'active' : 'normal'} />
          <Paragraph style={{ marginTop: 16, whiteSpace: 'pre-wrap' }}>{progressText}</Paragraph>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            当前为阶段提示，实际耗时会因文档页数和内容复杂度而不同。
          </Paragraph>
        </div>
      </Modal>

      <Drawer
        title="方法提取结果"
        open={drawerVisible}
        onClose={() => setDrawerVisible(false)}
        size="large"
        footer={
          <div style={{ textAlign: 'right' }}>
            <Space>
              <Button onClick={() => setDrawerVisible(false)}>取消</Button>
              <Button
                type="primary"
                onClick={() => {
                  void handleImport();
                }}
                disabled={importBlocked}
                loading={importing || redirecting}
              >
                保存
              </Button>
              <Button
                onClick={() => {
                  void handleImportAndUploadFile();
                }}
                disabled={importBlocked}
                loading={importing || redirecting}
              >
                保存并添加文件
              </Button>
            </Space>
          </div>
        }
      >
        {!methodEditors.length ? (
          <Spin tip="暂无数据" />
        ) : (
          <Tabs
            activeKey={activeMethodTab}
            onChange={setActiveMethodTab}
            items={methodEditors.map((editor, index) => ({
              key: editor.key,
              label: editor.method.name?.trim() || `方法${index + 1}`,
              children: (
                <div>
                  {!editor.nameDuplicate.duplicate && (
                    <Alert
                      type="success"
                      showIcon
                      message="名称检查"
                      description="当前方法名称在数据库中没有重复。"
                      style={{ marginBottom: 16 }}
                    />
                  )}
                  {editor.nameDuplicate.duplicate && editor.duplicateChoice === 'skip' && (
                    <Alert
                      type="info"
                      showIcon
                      message="已选择跳过"
                      description="本方法将不保存到数据库（不解析入库）。"
                      style={{ marginBottom: 16 }}
                      action={
                        <Button size="small" onClick={() => setDuplicateAction(editor.key, 'clear')}>
                          取消跳过
                        </Button>
                      }
                    />
                  )}
                  {editor.nameDuplicate.duplicate
                    && editor.duplicateChoice === 'replace'
                    && !!editor.replaceNodeId
                    && (
                    <Alert
                      type="info"
                      showIcon
                      message="将覆盖库中已有方法"
                      description={`以本次解析内容更新：${(editor.nameDuplicate.existing as GraphNode | undefined)?.name || editor.method.name}（${editor.replaceNodeId}）`}
                      style={{ marginBottom: 16 }}
                      action={
                        <Button size="small" onClick={() => setDuplicateAction(editor.key, 'clear')}>
                          改回新建
                        </Button>
                      }
                    />
                  )}
                  {editor.nameDuplicate.duplicate
                    && editor.duplicateChoice !== 'skip'
                    && !(editor.duplicateChoice === 'replace' && editor.replaceNodeId) && (
                    <Alert
                      type="warning"
                      showIcon
                      message="名称与库中已有方法重复"
                      description={
                        <div>
                          <Text>
                            与以下记录名称相同：{(editor.nameDuplicate.existing as GraphNode | undefined)?.name || '—'}
                            {' '}
                            （{String((editor.nameDuplicate.existing as GraphNode | undefined)?.id || '—')}）
                          </Text>
                          <div style={{ marginTop: 12 }}>
                            <Space wrap>
                              <Button
                                type="primary"
                                onClick={() =>
                                  setDuplicateAction(
                                    editor.key,
                                    'replace',
                                    String((editor.nameDuplicate.existing as GraphNode | undefined)?.id || ''),
                                  )
                                }
                              >
                                替换
                              </Button>
                              <Button onClick={() => setDuplicateAction(editor.key, 'skip')}>跳过（不保存）</Button>
                              <Button onClick={() => openRenameForEditor(editor.key)}>重命名</Button>
                            </Space>
                            {editor.showRenameInput ? (
                              <Space.Compact style={{ marginTop: 12, maxWidth: 480, width: '100%' }}>
                                <Input
                                  placeholder="输入新的方法名称"
                                  value={editor.renameDraft}
                                  onChange={(e) => setRenameDraftValue(editor.key, e.target.value)}
                                  onPressEnter={() => applyRenameDraft(editor.key)}
                                />
                                <Button type="primary" onClick={() => applyRenameDraft(editor.key)}>应用</Button>
                              </Space.Compact>
                            ) : null}
                          </div>
                        </div>
                      }
                      style={{ marginBottom: 16 }}
                    />
                  )}
                  <Title level={5}>方法信息</Title>
                  <Form layout="vertical" style={{ marginBottom: 24 }}>
                    <Row gutter={16}>
                      <Col xs={24} md={12}>
                        <Form.Item label="名称" required>
                          <Input value={editor.method.name} onChange={(e) => onMethodNameChange(editor.key, e.target.value)} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item label="标签">
                          <Input value={editor.method.label} onChange={(e) => updateEditableField(editor.key, 'label', e.target.value)} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Form.Item label="描述">
                      <TextArea
                        rows={3}
                        value={editor.method.description}
                        onChange={(e) => updateEditableField(editor.key, 'description', e.target.value)}
                      />
                    </Form.Item>
                    <Row gutter={16}>
                      <Col xs={24} md={12}>
                        <Form.Item label="来源文件">
                          <Select
                            showSearch
                            allowClear
                            placeholder="选择文件节点"
                            optionFilterProp="label"
                            style={{ width: '100%' }}
                            value={editor.method.source_id || undefined}
                            options={fileSelectOptions}
                            onChange={(id) => updateFileSource(editor.key, (id as string) || null)}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item label="方法类别">
                          <Select<MethodClassKind>
                            value={editor.method.methodClass}
                            options={[
                              { value: '总则', label: '总则' },
                              { value: '分则', label: '分则' },
                            ]}
                            onChange={(v) => updateEditableField(editor.key, 'methodClass', v)}
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Form.Item
                      label={(
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 8 }}>
                          <span>试验条件</span>
                          <Button
                            type="text"
                            size="small"
                            icon={editor.mdFieldEdit.sytj ? <CheckOutlined /> : <EditOutlined />}
                            onClick={() => toggleMdField(editor.key, 'sytj')}
                            aria-label={editor.mdFieldEdit.sytj ? '完成编辑' : '编辑'}
                          />
                        </div>
                      )}
                    >
                      {editor.mdFieldEdit.sytj ? (
                        <TextArea rows={8} value={editor.method.sytj} onChange={(e) => updateEditableField(editor.key, 'sytj', e.target.value)} />
                      ) : (
                        <div
                          style={{
                            border: '1px solid #f0f0f0',
                            borderRadius: 6,
                            padding: 12,
                            background: '#fafafa',
                            minHeight: 100,
                            overflow: 'auto',
                          }}
                        >
                          {(editor.method.sytj || '').trim() ? (
                            <MarkdownRenderer content={editor.method.sytj} />
                          ) : (
                            <Text type="secondary">暂无内容</Text>
                          )}
                        </div>
                      )}
                    </Form.Item>
                    <Form.Item
                      label={(
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 8 }}>
                          <span>试验过程</span>
                          <Button
                            type="text"
                            size="small"
                            icon={editor.mdFieldEdit.sygc ? <CheckOutlined /> : <EditOutlined />}
                            onClick={() => toggleMdField(editor.key, 'sygc')}
                            aria-label={editor.mdFieldEdit.sygc ? '完成编辑' : '编辑'}
                          />
                        </div>
                      )}
                    >
                      {editor.mdFieldEdit.sygc ? (
                        <TextArea rows={10} value={editor.method.sygc} onChange={(e) => updateEditableField(editor.key, 'sygc', e.target.value)} />
                      ) : (
                        <div
                          style={{
                            border: '1px solid #f0f0f0',
                            borderRadius: 6,
                            padding: 12,
                            background: '#fafafa',
                            minHeight: 120,
                            overflow: 'auto',
                          }}
                        >
                          {(editor.method.sygc || '').trim() ? (
                            <MarkdownRenderer content={editor.method.sygc} />
                          ) : (
                            <Text type="secondary">暂无内容</Text>
                          )}
                        </div>
                      )}
                    </Form.Item>
                    <Form.Item
                      label={(
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 8 }}>
                          <span>结果评定</span>
                          <Button
                            type="text"
                            size="small"
                            icon={editor.mdFieldEdit.jgpd ? <CheckOutlined /> : <EditOutlined />}
                            onClick={() => toggleMdField(editor.key, 'jgpd')}
                            aria-label={editor.mdFieldEdit.jgpd ? '完成编辑' : '编辑'}
                          />
                        </div>
                      )}
                    >
                      {editor.mdFieldEdit.jgpd ? (
                        <TextArea rows={6} value={editor.method.jgpd} onChange={(e) => updateEditableField(editor.key, 'jgpd', e.target.value)} />
                      ) : (
                        <div
                          style={{
                            border: '1px solid #f0f0f0',
                            borderRadius: 6,
                            padding: 12,
                            background: '#fafafa',
                            minHeight: 100,
                            overflow: 'auto',
                          }}
                        >
                          {(editor.method.jgpd || '').trim() ? (
                            <MarkdownRenderer content={editor.method.jgpd} />
                          ) : (
                            <Text type="secondary">暂无内容</Text>
                          )}
                        </div>
                      )}
                    </Form.Item>
                    <Row gutter={16}>
                      <Col xs={24} md={8}>
                        <Form.Item label="状态">
                          <Select
                            value={editor.method.status}
                            options={METHOD_STATUS_OPTIONS}
                            onChange={(v) => updateEditableField(editor.key, 'status', v)}
                            style={{ width: '100%' }}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={8}>
                        <Form.Item label="修改用户">
                          <Input readOnly value={displayMethodSavedUser(editor)} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={8}>
                        <Form.Item label="修改时间">
                          <Input readOnly value={displayMethodSavedTime(editor)} />
                        </Form.Item>
                      </Col>
                    </Row>
                  </Form>

                  <Title level={5} style={{ marginTop: 0 }}>
                    图谱关联（方案 B：目标节点 → 本方法）
                  </Title>
                  <Paragraph type="secondary" style={{ marginBottom: 12, marginTop: 0 }}>
                    关系类型仅 CON、HAS、BELONG_TO。切换「关联节点类型」会清空「具体节点」。无目标 id 或关联度不合法时该行不会写入 Neo4j。
                  </Paragraph>
                  <Table<MethodRelationRow>
                    size="small"
                    pagination={false}
                    rowKey="key"
                    dataSource={editor.relationRows}
                    scroll={{ x: 'max-content' }}
                    columns={[
                      {
                        title: '关联节点类型',
                        dataIndex: 'targetType',
                        width: 130,
                        render: (_, row) => (
                          <Select
                            style={{ width: 120 }}
                            value={row.targetType}
                            options={RELATION_TARGET_TYPES.map((o) => ({ value: o.value, label: o.label }))}
                            onChange={(v) => updateRelationRow(editor.key, row.key, { targetType: v as RelationTargetType })}
                          />
                        ),
                      },
                      {
                        title: '具体节点',
                        dataIndex: 'targetId',
                        width: 220,
                        render: (_, row) => {
                          const list = nodeOptionsByType[row.targetType] || [];
                          return (
                            <Select
                              showSearch
                              allowClear
                              placeholder="选择节点"
                              style={{ minWidth: 200 }}
                              optionFilterProp="label"
                              value={row.targetId || undefined}
                              options={list.map((n) => ({
                                value: n.id,
                                label: String(n.name || n.label || n.id),
                              }))}
                              onChange={(id) => updateRelationRow(editor.key, row.key, { targetId: (id as string) || '' })}
                            />
                          );
                        },
                      },
                      {
                        title: '关系',
                        dataIndex: 'relType',
                        width: 120,
                        render: (_, row) => (
                          <Select
                            style={{ width: 110 }}
                            value={row.relType}
                            options={METHOD_REL_TYPES}
                            onChange={(v) => updateRelationRow(editor.key, row.key, { relType: v as RelTypeKind })}
                          />
                        ),
                      },
                      {
                        title: '关联度',
                        dataIndex: 'score',
                        width: 110,
                        render: (_, row) => (
                          <InputNumber
                            style={{ width: '100%' }}
                            min={-999}
                            max={999}
                            step={0.01}
                            value={row.score == null ? undefined : row.score}
                            onChange={(v) => updateRelationRow(editor.key, row.key, { score: v == null ? null : Number(v) })}
                          />
                        ),
                      },
                      {
                        title: '操作',
                        key: 'op',
                        width: 72,
                        render: (_, row) => (
                          <Button
                            type="text"
                            danger
                            size="small"
                            icon={<DeleteOutlined />}
                            onClick={() => removeRelationRow(editor.key, row.key)}
                            aria-label="删除行"
                          />
                        ),
                      },
                    ]}
                  />
                  <Button
                    type="dashed"
                    icon={<PlusOutlined />}
                    onClick={() => addRelationRow(editor.key)}
                    block
                    style={{ marginBottom: 16 }}
                  >
                    添加关联行
                  </Button>

                  {/* <div style={{ marginBottom: 16, padding: 12, background: '#f5f5f5', borderRadius: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <Text strong>关联管理：</Text>
                      <div style={{ marginTop: 8 }}>
                        <Text type="secondary">
                          保存方法后，可在此用「管理关联」在图库中继续维护与设备、环境、项目、指标、文件等关系。
                        </Text>
                      </div>
                    </div>
                    <Button icon={<LinkOutlined />} onClick={() => handleOpenRelationModal(editor)}>
                      管理关联
                    </Button>
                  </div> */}
                </div>
              ),
            }))}
          />
        )}
      </Drawer>

      <RelationModal
        open={relationModal.open}
        nodeId={relationModal.node.id}
        nodeType="TestMethod"
        nodeName={relationModal.node.name || ''}
        onClose={() => setRelationModal({ open: false, node: {} as GraphNode })}
        onSaved={() => { onRefresh(); }}
      />
    </>
  );
}
