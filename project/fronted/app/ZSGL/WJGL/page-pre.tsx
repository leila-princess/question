'use client';

import React, { useState, useEffect, useCallback, useRef, useLayoutEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  Card, Tabs, Table, Button, Space, Input, Modal, Form,
  Select, Tag, message, Upload, Popconfirm, Switch,
  Table as AntTable, Spin, DatePicker, Tree, Checkbox
} from 'antd';
import {
  FolderOutlined, PlusOutlined, EditOutlined,
  DeleteOutlined, CloudUploadOutlined, ScanOutlined, ReadOutlined,
  ExperimentOutlined
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';

const { Option } = Select;

const TAB_CATEGORIES: Record<string, string> = {
  standard: '法规标准',
  normative: '规范性文件',
  guidance: '指导性文件',
  scheme: '试验方案',
  outline: '试验大纲',
  report: '试验报告',
  issue: '问题',
  assessment: '专项评估报告',
  other: '其他',
};

const NODE_TYPES = [
  { value: 'EquipmentType', label: '设备类型', color: '#1890ff' },
  { value: 'Equipment', label: '设备', color: '#52c41a' },
  { value: 'Environment', label: '环境', color: '#722ed1' },
  { value: 'TestProject', label: '项目', color: '#fa8c16' },
  { value: 'Indicator', label: '指标', color: '#13c2c2' },
  { value: 'TestMethod', label: '方法', color: '#eb2f96' },
];

interface MinIOFile {
  object_name: string;
  doc_id: string;
  title: string;
  category: string;
  file_type: string;
  file_label: string;
  ics: string;
  ccs: string;
  std_code: string;
  upload_userid: string;
  upload_time: string;
  file_size: number;
  abstract: string;
  keywords: string;
}

interface AssocRow {
  key: string;
  node_type: string;
  node_ids: string[];
  nodes: { id: string; name: string }[];
}

/** 写入 MinIO 的上传者展示名：优先用户名，避免把 userId 当展示名造成「乱码感」 */
function getUploadUserLabelForSubmit(): string {
  if (typeof window === 'undefined') return '未登录用户';
  const name = (localStorage.getItem('userName') || '').trim();
  if (name) return name.slice(0, 80);
  return '未登录用户';
}

interface UploadFileEntry {
  uid: string;
  file: File;
  name: string;
  size: string;
  parsed: boolean;
  parsing: boolean;
  syncChroma: boolean;
  associations: AssocRow[];
  isAssociated: boolean;
  meta: {
    doc_id: string;
    title: string;
    ics: string;
    ccs: string;
    abstract: string;
    keywords: string[];
    file_type: string;
    needs_manual_ics: boolean;
    needs_manual_ccs: boolean;
    std_code: string;
    std_name: string;
  };
}

export default function WJGLPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState('standard');
  const [files, setFiles] = useState<MinIOFile[]>([]);
  const [treeData, setTreeData] = useState<DataNode[]>([]);
  const [selectedTreeKey, setSelectedTreeKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [uploadFileList, setUploadFileList] = useState<UploadFileEntry[]>([]);
  const [uploadLabel, setUploadLabel] = useState('');
  const [allSyncChroma, setAllSyncChroma] = useState(false);
  const [duplicateAction, setDuplicateAction] = useState<'check' | 'replace' | 'skip'>('replace');

  const [assocModalOpen, setAssocModalOpen] = useState(false);
  const [assocTargetRowUid, setAssocTargetRowUid] = useState<string | null>(null);
  const [assocRows, setAssocRows] = useState<AssocRow[]>([]);
  const [neo4jNodesCache, setNeo4jNodesCache] = useState<Record<string, { id: string; name: string }[]>>({});
  const [nodesLoading, setNodesLoading] = useState<Record<string, boolean>>({});

  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingFile, setEditingFile] = useState<MinIOFile | null>(null);
  const [editForm] = Form.useForm();

  const [otherAddOpen, setOtherAddOpen] = useState(false);
  const [otherAddForm] = Form.useForm();
  const [otherAddFile, setOtherAddFile] = useState<File | null>(null);
  const [otherAddLoading, setOtherAddLoading] = useState(false);

  const [filters, setFilters] = useState({ keyword: '', stdCode: '', uploadUser: '', dateFrom: '', dateTo: '' });
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({});

  const [extractModalOpen, setExtractModalOpen] = useState(false);
  const [extractTargetFile, setExtractTargetFile] = useState<MinIOFile | null>(null);
  const [extractUploadEntry, setExtractUploadEntry] = useState<UploadFileEntry | null>(null);
  const [extractToc, setExtractToc] = useState<DataNode[]>([]);
  const [extractTocLoading, setExtractTocLoading] = useState(false);
  const [extractCheckedKeys, setExtractCheckedKeys] = useState<string[]>([]);
  const [extractCheckedNodes, setExtractCheckedNodes] = useState<Array<{ sectionNo: string; title: string }>>([]);
  const [extractSubmitting, setExtractSubmitting] = useState(false);

  const tableAreaRef = useRef<HTMLDivElement>(null);
  const [tableScrollY, setTableScrollY] = useState(360);

  useLayoutEffect(() => {
    const el = tableAreaRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const measure = () => {
      const h = el.getBoundingClientRect().height;
      // 表头 + 分页 + 边框余量（仅表体区域滚动）
      setTableScrollY(Math.max(160, Math.floor(h - 120)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const category = TAB_CATEGORIES[activeTab] || '其他';
  const isStandard = activeTab === 'standard';
  const isOther = activeTab === 'other';

  const fetchCategoryCounts = useCallback(async () => {
    try {
      const res = await fetch('/api/wjgl1/category-counts');
      const data = await res.json();
      if (data.success && data.data && typeof data.data === 'object') {
        setCategoryCounts(data.data as Record<string, number>);
      }
    } catch {
      /* Tab 仍可用，数量保持上次或 0 */
    }
  }, []);

  useEffect(() => {
    void fetchCategoryCounts();
  }, [fetchCategoryCounts]);

  const tabItems = useMemo(
    () =>
      Object.entries(TAB_CATEGORIES).map(([key, label]) => ({
        key,
        label: (
          <span>
            <FolderOutlined /> {label}{' '}
            <span style={{ color: 'rgba(0,0,0,0.45)', fontWeight: 400 }}>({categoryCounts[label] ?? 0})</span>
          </span>
        ),
      })),
    [categoryCounts],
  );

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ category });
      if (filters.keyword) params.set('keyword', filters.keyword);
      if (filters.stdCode) params.set('std_code', filters.stdCode);
      if (filters.uploadUser) params.set('upload_user', filters.uploadUser);
      if (filters.dateFrom) params.set('upload_time_from', filters.dateFrom);
      if (filters.dateTo) params.set('upload_time_to', filters.dateTo);
      const res = await fetch(`/api/wjgl1/list?${params}`);
      const data = await res.json();
      if (data.success) setFiles(data.data);
    } catch {
      message.error('获取文件列表失败');
    } finally {
      setLoading(false);
    }
  }, [category, filters.keyword, filters.stdCode, filters.uploadUser, filters.dateFrom, filters.dateTo]);

  const fetchTree = useCallback(async () => {
    try {
      const endpoint = isStandard ? '/api/wjgl1/ccs-tree' : '/api/wjgl1/label-tree';
      const res = await fetch(`${endpoint}?category=${category}`);
      const data = await res.json();
      if (data.success) {
        const nodes: DataNode[] = data.data.map((item: any) => ({
          title: item.title,
          key: item.key,
          children: item.children?.map((c: any) => ({
            title: c.std_code || c.title,
            key: c.object_name,
            isLeaf: true,
          })),
        }));
        setTreeData(nodes);
      }
    } catch {
      message.error('获取分类树失败');
    }
  }, [category, isStandard]);

  useEffect(() => { fetchFiles(); fetchTree(); }, [fetchFiles, fetchTree]);

  useEffect(() => {
    if (!isStandard) setFilters(p => ({ ...p, stdCode: '' }));
  }, [activeTab, isStandard]);

  const resetUploadState = () => {
    setUploadFileList([]);
    setUploadLabel('');
    setAllSyncChroma(false);
  };

  const openUploadModal = () => {
    resetUploadState();
    setUploadModalOpen(true);
  };

  const closeUploadModal = () => {
    resetUploadState();
    setUploadModalOpen(false);
  };

  const handleFileSelect = (file: File) => {
    const entry: UploadFileEntry = {
      uid: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      name: file.name,
      size: `${(file.size / 1024).toFixed(1)} KB`,
      parsed: false,
      parsing: false,
      syncChroma: allSyncChroma,
      associations: [],
      isAssociated: false,
      meta: {
        doc_id: '',
        title: file.name.replace(/\.[^/.]+$/, ''),
        ics: '',
        ccs: '',
        abstract: '',
        keywords: [],
        file_type: file.name.split('.').pop() || '',
        needs_manual_ics: true,
        needs_manual_ccs: true,
        std_code: '',
        std_name: '',
      },
    };
    setUploadFileList(prev => [...prev, entry]);
    return false;
  };

  const handleRemoveFile = (uid: string) => {
    setUploadFileList(prev => prev.filter(e => e.uid !== uid));
  };

  const handleAutoParse = async () => {
    const unparsed = uploadFileList.filter(e => !e.parsed && !e.parsing);
    if (!unparsed.length) { message.info('所有文件已解析'); return; }

    setUploadFileList(prev => prev.map(e => e.parsed ? e : { ...e, parsing: true }));

    for (const entry of unparsed) {
      try {
        const formData = new FormData();
        formData.append('file', entry.file);
        const res = await fetch('/api/wjgl1/auto-parse', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.success) {
          const m = data.data;
          const warns = Array.isArray(m?.warnings) ? m.warnings.filter(Boolean) : [];
          if (warns.length) {
            message.warning(`${entry.name}：${warns.join(' ')}`, 8);
          } else if (m?.llm_used === false) {
            message.warning(
              `${entry.name}：未调用 LLM，仅解析文件名。请检查后端 .env 中 OPENAI_API_KEY 并访问 /api/wjgl1/auto-parse/status`,
              8,
            );
          }
          setUploadFileList(prev => prev.map(e =>
            e.uid === entry.uid ? {
              ...e,
              parsed: true,
              parsing: false,
              meta: {
                doc_id: m.doc_id,
                title: m.title,
                ics: m.ics || '',
                ccs: m.ccs || '',
                abstract: m.abstract || '',
                keywords: m.keywords || [],
                file_type: m.file_type,
                needs_manual_ics: m.needs_manual_ics,
                needs_manual_ccs: m.needs_manual_ccs,
                std_code: m.std_code || '',
                std_name: m.std_name || '',
              },
            } : e
          ));
        } else {
          setUploadFileList(prev => prev.map(e => e.uid === entry.uid ? { ...e, parsing: false } : e));
          message.warning(`${entry.name} 解析失败: ${data.error}`);
        }
      } catch (err: any) {
        setUploadFileList(prev => prev.map(e => e.uid === entry.uid ? { ...e, parsing: false } : e));
        message.error(`${entry.name} 解析失败: ${err?.message || '网络错误，请检查后端是否运行'}`);
      }
    }
  };

  const openAssocForFile = (uid: string) => {
    setAssocTargetRowUid(uid);
    const entry = uploadFileList.find(e => e.uid === uid);
    setAssocRows(entry?.associations || []);
    setAssocModalOpen(true);
  };

  const saveAssocForFile = () => {
    if (!assocTargetRowUid) return;
    setUploadFileList(prev => prev.map(e =>
      e.uid === assocTargetRowUid ? { ...e, associations: [...assocRows], isAssociated: assocRows.length > 0 } : e
    ));
    setAssocModalOpen(false);
    setAssocTargetRowUid(null);
    setAssocRows([]);
  };

  const fetchNeo4jNodes = async (nodeType: string) => {
    if (neo4jNodesCache[nodeType]) return neo4jNodesCache[nodeType];
    setNodesLoading(prev => ({ ...prev, [nodeType]: true }));
    try {
      const res = await fetch(`/api/wjgl1/neo4j-nodes?node_type=${nodeType}`);
      const data = await res.json();
      if (data.success) {
        setNeo4jNodesCache(prev => ({ ...prev, [nodeType]: data.data }));
        return data.data;
      }
    } catch { }
    finally {
      setNodesLoading(prev => ({ ...prev, [nodeType]: false }));
    }
    return [];
  };

  const addAssocRow = () => {
    setAssocRows(prev => [...prev, { key: `assoc-${Date.now()}-${Math.random().toString(36).slice(2, 5)}`, node_type: '', node_ids: [], nodes: [] }]);
  };

  const removeAssocRow = (key: string) => {
    setAssocRows(prev => prev.filter(r => r.key !== key));
  };

  const updateFileMeta = (uid: string, metaUpdates: Partial<UploadFileEntry['meta']>) => {
    setUploadFileList(prev => prev.map(e =>
      e.uid === uid ? { ...e, meta: { ...e.meta, ...metaUpdates } } : e
    ));
  };

  const updateFileEntry = (uid: string, updates: Partial<UploadFileEntry>) => {
    setUploadFileList(prev => prev.map(e => e.uid === uid ? { ...e, ...updates } : e));
  };

  const handleUpload = () => {
    if (!uploadFileList.length) { message.warning('请先选择文件'); return; }
    const allParsed = uploadFileList.every(e => e.parsed);
    if (!allParsed) { message.warning('请先点击"自动解析"完成所有文件解析'); return; }

    const formData = new FormData();
    uploadFileList.forEach(entry => {
      formData.append('files', entry.file);
      formData.append(`uid_${entry.name}`, entry.uid);
      formData.append(`title_${entry.uid}`, entry.meta.title);
      formData.append(`abstract_${entry.uid}`, entry.meta.abstract);
      formData.append(`keywords_${entry.uid}`, JSON.stringify(entry.meta.keywords));
      formData.append(`ics_${entry.uid}`, entry.meta.ics);
      formData.append(`ccs_${entry.uid}`, entry.meta.ccs);
      formData.append(`std_code_${entry.uid}`, entry.meta.std_code || '');
      formData.append(`std_name_${entry.uid}`, entry.meta.std_name || '');
      formData.append(`sync_chroma_${entry.uid}`, String(entry.syncChroma));
      formData.append(`associations_${entry.uid}`, JSON.stringify(
        entry.associations.map(r => ({ node_type: r.node_type, node_ids: r.node_ids }))
      ));
    });
    formData.append('category', category);
    formData.append('label', uploadLabel);
    formData.append('user_id', getUploadUserLabelForSubmit());
    formData.append('duplicate_action', duplicateAction);

    setLoading(true);
    fetch('/api/wjgl1/upload', { method: 'POST', body: formData })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => {
        if (data.success && Array.isArray(data.data)) {
          const okCount = data.data.filter((d: any) => d.success).length;
          message.success(`成功上传 ${okCount} 个文件`);
          closeUploadModal();
          fetchFiles();
          fetchTree();
          void fetchCategoryCounts();
        } else {
          console.error('Upload response:', data);
          message.error(data.error || '上传失败');
        }
      })
      .catch(err => {
        console.error('Upload error:', err);
        message.error(`上传失败: ${err.message}`);
      })
      .finally(() => setLoading(false));
  };

  const handleDelete = async (record: MinIOFile) => {
    try {
      const res = await fetch('/api/wjgl1/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_name: record.object_name, doc_id: record.doc_id }),
      });
      const data = await res.json();
      if (data.success) { message.success('删除成功'); fetchFiles(); fetchTree(); void fetchCategoryCounts(); }
      else message.error(data.error || '删除失败');
    } catch { message.error('删除失败'); }
  };

  const handleEdit = (record: MinIOFile) => {
    setEditingFile(record);
    editForm.setFieldsValue({
      title: record.title,
      std_code: record.std_code,
      // ccs: record.ccs,
      abstract: record.abstract,
    });
    setEditModalOpen(true);
  };

  const handleEditSave = async () => {
    if (!editingFile) return;
    const values = await editForm.validateFields();
    try {
      const updates: Record<string, string> = {
        'title': values.title,
        'abstract': values.abstract,
      };
      if (isStandard) {
        updates['std-code'] = values.std_code || '';
        // updates['file-ccs'] = values.ccs || '';
      }
      const res = await fetch('/api/wjgl1/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          object_name: editingFile.object_name,
          doc_id: editingFile.doc_id,
          updates,
        }),
      });
      const data = await res.json();
      if (data.success) { message.success('更新成功'); setEditModalOpen(false); fetchFiles(); }
      else message.error(data.error || '更新失败');
    } catch { message.error('更新失败'); }
  };

  const handleOtherAddSubmit = async () => {
    try {
      const values = await otherAddForm.validateFields();
      if (!otherAddFile) {
        message.warning('请上传附件');
        return;
      }
      setOtherAddLoading(true);
      const fd = new FormData();
      fd.append('file', otherAddFile);
      fd.append('title', values.name as string);
      fd.append('abstract', (values.abstract as string) || '');
      fd.append('upload_user', values.upload_user as string);
      fd.append('upload_time', dayjs(values.upload_time).format('YYYY-MM-DD'));
      fd.append('category', '其他');
      const res = await fetch('/api/wjgl1/simple-upload', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.success) {
        message.success('新增成功');
        setOtherAddOpen(false);
        setOtherAddFile(null);
        otherAddForm.resetFields();
        fetchFiles();
        fetchTree();
        void fetchCategoryCounts();
      } else {
        message.error(data.error || '新增失败');
      }
    } catch {
      /* 校验未通过 */
    } finally {
      setOtherAddLoading(false);
    }
  };

  const openExtractModal = async (record: MinIOFile) => {
    setExtractTargetFile(record);
    setExtractUploadEntry(null);
    setExtractModalOpen(true);
    setExtractTocLoading(true);
    setExtractCheckedKeys([]);
    setExtractCheckedNodes([]);
    try {
      const params = new URLSearchParams();
      if (record.object_name) params.set('object_name', record.object_name);
      else if (record.doc_id) params.set('doc_id', record.doc_id);
      const encodedParams = params.toString();
      const url = `/api/wjgl1/document-toc?${encodedParams}`;
      console.log('[document-toc] request:', { object_name: record.object_name, url });
      const res = await fetch(url);
      const data = await res.json();
      console.log('[document-toc] response:', data);
      if (data.success && data.data?.toc) {
        console.log('[document-toc] toc:', data.data.toc.length, 'top-level nodes');
        const treeNodes = buildTreeNodes(data.data.toc);
        console.log('[document-toc] treeNodes:', treeNodes.length, 'nodes');
        setExtractToc(treeNodes);
      } else {
        message.warning(data.error || '获取目录失败');
        setExtractToc([]);
      }
    } catch (e: any) {
      message.error(`获取目录失败: ${e?.message || '网络错误'}`);
      setExtractToc([]);
    } finally {
      setExtractTocLoading(false);
    }
  };

  const openExtractModalForUploadEntry = async (entry: UploadFileEntry) => {
    setExtractUploadEntry(entry);
    setExtractTargetFile(null);
    setExtractModalOpen(true);
    setExtractTocLoading(true);
    setExtractCheckedKeys([]);
    setExtractCheckedNodes([]);
    try {
      const formData = new FormData();
      formData.append('file', entry.file);
      const res = await fetch('/api/wjgl1/document-toc-upload', { method: 'POST', body: formData });
      const data = await res.json();
      console.log('[document-toc-upload] response:', data);
      if (data.success && data.data?.toc) {
        console.log('[document-toc-upload] toc:', JSON.stringify(data.data.toc, null, 2).slice(0, 500));
        const treeNodes = buildTreeNodes(data.data.toc);
        console.log('[document-toc-upload] treeNodes:', treeNodes.length, 'top-level nodes');
        setExtractToc(treeNodes);
      } else {
        message.warning(data.error || '获取目录失败');
        setExtractToc([]);
      }
    } catch (e: any) {
      message.error(`获取目录失败: ${e?.message || '网络错误'}`);
      setExtractToc([]);
    } finally {
      setExtractTocLoading(false);
    }
  };

  const buildTreeNodes = (items: any[], prefix = '', level = 1): DataNode[] => {
    return items.map((item, index) => {
      const key = item.sectionNo ? `${prefix}${item.sectionNo}` : `${prefix}title-${index}`;
      const displayTitle = item.sectionNo ? `${item.sectionNo} ${item.title}` : item.title;
      return {
        title: displayTitle,
        key,
        children: item.children && item.children.length > 0 ? buildTreeNodes(item.children, `${key}-`, level + 1) : undefined,
        sectionNo: item.sectionNo,
        rawTitle: item.title,
        level: level,
      } as DataNode & { sectionNo?: string; rawTitle?: string; level?: number };
    });
  };

  const getAllChildKeys = (node: DataNode): string[] => {
    const keys: string[] = [];
    if (node.children) {
      for (const child of node.children) {
        keys.push(String(child.key));
        keys.push(...getAllChildKeys(child));
      }
    }
    return keys;
  };

  const handleExtractCheck = (checkedKeys: any, info: any) => {
    const keys = checkedKeys?.checked || (Array.isArray(checkedKeys) ? checkedKeys : []);
    const node = info.node;
    const checked = info.checked;

    if (node && node.children && node.children.length > 0) {
      const allChildKeys = getAllChildKeys(node);
      if (checked) {
        // 选中父级 -> 自动添加所有子级
        const newKeys = [...new Set([...keys, ...allChildKeys])];
        setExtractCheckedKeys(newKeys);
      } else {
        // 取消父级 -> 移除所有子级
        const newKeys = keys.filter((k: string) => !allChildKeys.includes(k));
        setExtractCheckedKeys(newKeys);
      }
    } else {
      setExtractCheckedKeys(keys);
    }
  };

  const flattenTreeNodes = (nodes: DataNode[]): any[] => {
    const result: any[] = [];
    for (const node of nodes) {
      result.push(node);
      if (node.children && node.children.length > 0) {
        result.push(...flattenTreeNodes(node.children));
      }
    }
    return result;
  };

  const handleExtractSubmit = async () => {
    if (extractCheckedKeys.length === 0) {
      message.warning('请至少选择一个章节');
      return;
    }
    setExtractSubmitting(true);
    try {
      const allNodes = flattenTreeNodes(extractToc);
      const checkedNodes = allNodes.filter((n: any) => extractCheckedKeys.includes(n.key));

      // 构建父节点映射表（从整个树结构）
      const parentMap = new Map<string, any>();
      const buildParentMap = (nodes: any[], parent?: any) => {
        for (const node of nodes) {
          if (parent) parentMap.set(String(node.key), parent);
          if (node.children) buildParentMap(node.children, node);
        }
      };
      buildParentMap(extractToc);

      // 找出"被选择的最外层"节点：选中的节点中，其父节点没有被选中的
      const checkedKeysSet = new Set(extractCheckedKeys);
      const topLevelSelected: any[] = [];
      const childOfSelected: any[] = [];

      for (const node of checkedNodes) {
        const parent = parentMap.get(String(node.key));
        if (parent && checkedKeysSet.has(String(parent.key))) {
          childOfSelected.push(node);
        } else {
          topLevelSelected.push(node);
        }
      }

      // 按最外层选中节点分组
      const grouped: Record<string, { parent: any; children: any[] }> = {};
      for (const node of topLevelSelected) {
        const key = String(node.key);
        grouped[key] = {
          parent: {
            sectionNo: node.sectionNo || '',
            title: node.rawTitle || node.title || '',
            level: node.level || 1,
          },
          children: [],
        };
      }

      // 将子节点分配到对应的最外层父节点下
      for (const child of childOfSelected) {
        let cur = child;
        let assigned = false;
        while (cur) {
          const p = parentMap.get(String(cur.key));
          if (!p) break;
          const parentKey = String(p.key);
          if (grouped[parentKey]) {
            grouped[parentKey].children.push({
              sectionNo: child.sectionNo || '',
              title: child.rawTitle || child.title || '',
              level: child.level || 1,
            });
            assigned = true;
            break;
          }
          cur = p;
        }
      }

      const sectionsData = Object.values(grouped).map(g => ({
        parent: g.parent,
        children: g.children,
      }));

      if (sectionsData.length === 0) {
        message.warning('请至少选择一个章节');
        return;
      }

      console.log('[method-extract] grouped sections:', sectionsData);

      if (extractUploadEntry) {
        const formData = new FormData();
        formData.append('file', extractUploadEntry.file);
        formData.append('sections', JSON.stringify(sectionsData));
        formData.append('user_id', getUploadUserLabelForSubmit());
        const res = await fetch('/api/FLGL_method/method-extract-sections-file', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json();
        if (data.success && data.data) {
          sessionStorage.setItem('methodExtractResult', JSON.stringify(data.data));
          message.success(`方法提取完成（共 ${data.data.methods_count || data.data.methods?.length || 0} 个方法）`);
          setExtractModalOpen(false);
          setExtractUploadEntry(null);
          setExtractTargetFile(null);
          router.push('/FLGL?tab=method&autoOpen=1');
        } else {
          message.error(data.error || '提取失败');
        }
      } else if (extractTargetFile) {
        const params = {
          object_name: extractTargetFile.object_name,
          source_file_id: `WJ_${extractTargetFile.doc_id}`,
          sections: sectionsData,
          user_id: getUploadUserLabelForSubmit(),
        };
        const res = await fetch('/api/FLGL_method/method-extract-sections', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        });
        const data = await res.json();
        if (data.success && data.data) {
          sessionStorage.setItem('methodExtractResult', JSON.stringify(data.data));
          message.success(`方法提取完成（共 ${data.data.methods_count || data.data.methods?.length || 0} 个方法）`);
          setExtractModalOpen(false);
          setExtractUploadEntry(null);
          setExtractTargetFile(null);
          router.push('/FLGL?tab=method&autoOpen=1');
        } else {
          message.error(data.error || '提取失败');
        }
      }
    } catch (e: any) {
      message.error(`提取失败: ${e?.message || '网络错误'}`);
    } finally {
      setExtractSubmitting(false);
    }
  };

  const mainColumns: ColumnsType<MinIOFile> = [
    { title: '文件名', dataIndex: 'title', key: 'title', ellipsis: true, width: 280 },
    ...(isStandard ? [
      { title: '标准号', dataIndex: 'std_code', key: 'std_code', width: 120 },
    ] : []),
    // { title: '文档ID', dataIndex: 'doc_id', key: 'doc_id', width: 100 },
    // ...(isStandard ? [
    //   { title: 'CCS', dataIndex: 'ccs', key: 'ccs', width: 70 },
    // ] : []),
    { title: '摘要', dataIndex: 'abstract', key: 'abstract', ellipsis: true, width: 200 },
    { title: '上传用户', dataIndex: 'upload_userid', key: 'upload_userid', width: 150 },
    {
      title: '上传时间', dataIndex: 'upload_time', key: 'upload_time', width: 180,
      render: (val: string) => {
        if (!val) return '-';
        try {
          return dayjs(val).format('YYYY-MM-DD');
        } catch {
          return val;
        }
      },
    },
    {
      title: '操作', key: 'action', width: 240, fixed: 'right' as const,
      render: (_: any, record: MinIOFile) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<ReadOutlined />}
            onClick={() => {
              router.push(`/Standards?object_name=${encodeURIComponent(record.object_name)}`);
            }}
          >
            阅读
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Button type="link" size="small" icon={<ExperimentOutlined />} onClick={() => openExtractModal(record)}>自动提取方法</Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const filteredFiles = files;

  const renderAssocTable = () => {
    const assocColumns: ColumnsType<AssocRow> = [
      {
        title: '关联节点类型', dataIndex: 'node_type', key: 'node_type', width: 160,
        render: (val: string, record: AssocRow) => (
          <Select
            value={val || undefined}
            style={{ width: '100%' }}
            placeholder="选择节点类型"
            onChange={async (v) => {
              const nodes = await fetchNeo4jNodes(v);
              setAssocRows(prev => prev.map(r => r.key === record.key ? { ...r, node_type: v, nodes, node_ids: [] } : r));
            }}
            loading={nodesLoading[val]}
          >
            {NODE_TYPES.map(nt => <Option key={nt.value} value={nt.value}>{nt.label}</Option>)}
          </Select>
        ),
      },
      {
        title: '关联具体节点', dataIndex: 'node_ids', key: 'node_ids',
        render: (_: string[], record: AssocRow) => (
          <Select
            mode="multiple"
            value={record.node_ids}
            style={{ width: '100%' }}
            placeholder={record.node_type ? "选择节点（可多选）" : "请先选择节点类型"}
            disabled={!record.node_type}
            loading={nodesLoading[record.node_type]}
            onChange={(vals) => {
              setAssocRows(prev => prev.map(r => r.key === record.key ? { ...r, node_ids: vals } : r));
            }}
          >
            {record.nodes?.map(n => <Option key={n.id} value={n.id}>{n.name}</Option>)}
          </Select>
        ),
      },
      {
        title: '', key: 'action', width: 60,
        render: (_: any, record: AssocRow) => (
          <Button type="link" danger size="small" onClick={() => removeAssocRow(record.key)}>删除</Button>
        ),
      },
    ];

    const usedTypes = new Set(assocRows.filter(r => r.node_type).map(r => r.node_type));
    return (
      <div>
        <AntTable columns={assocColumns} dataSource={assocRows} rowKey="key" pagination={false} size="small" />
        <Button type="dashed" block style={{ marginTop: 8 }} onClick={addAssocRow}>
          <PlusOutlined /> 新增关联行
        </Button>
        {usedTypes.size > 0 && (
          <Space wrap style={{ marginTop: 12 }}>
            {NODE_TYPES.filter(nt => usedTypes.has(nt.value)).map(nt => (
              <Tag key={nt.value} color={nt.color}>{nt.label}</Tag>
            ))}
          </Space>
        )}
      </div>
    );
  };

  const metaColumns: ColumnsType<UploadFileEntry> = [
    { title: '文件名', dataIndex: 'name', key: 'name', width: 150, ellipsis: true },
    ...(isStandard ? [
      {
        title: '标准号', dataIndex: ['meta', 'std_code'], key: 'std_code', width: 120, ellipsis: true,
        render: (val: string, record: UploadFileEntry) => (
          <Input value={val} size="small" onChange={e => updateFileMeta(record.uid, { std_code: e.target.value })} />
        ),
      },
      {
        title: '标准名称', dataIndex: ['meta', 'std_name'], key: 'std_name', width: 180, ellipsis: true,
        render: (val: string, record: UploadFileEntry) => (
          <Input value={val} size="small" onChange={e => updateFileMeta(record.uid, { std_name: e.target.value })} />
        ),
      },
      // {
      //   title: 'ICS', dataIndex: ['meta', 'ics'], key: 'ics', width: 90,
      //   render: (val: string, record: UploadFileEntry) => (
      //     <Input value={val} size="small"
      //       status={record.meta.needs_manual_ics && record.parsed ? 'warning' : undefined}
      //       onChange={e => updateFileMeta(record.uid, { ics: e.target.value })}
      //       placeholder={record.meta.needs_manual_ics ? '需手输' : ''}
      //     />
      //   ),
      // },
      // {
      //   title: 'CCS', dataIndex: ['meta', 'ccs'], key: 'ccs', width: 80,
      //   render: (val: string, record: UploadFileEntry) => (
      //     <Input value={val} size="small"
      //       status={record.meta.needs_manual_ccs && record.parsed ? 'warning' : undefined}
      //       onChange={e => updateFileMeta(record.uid, { ccs: e.target.value })}
      //       placeholder={record.meta.needs_manual_ccs ? '需手输' : ''}
      //     />
      //   ),
      // },
    ] : []),
    {
      title: '关键词', dataIndex: ['meta', 'keywords'], key: 'keywords', width: 140,
      render: (keywords: string[], record: UploadFileEntry) => (
        <Select mode="tags" value={keywords} size="small" style={{ width: '100%' }}
          onChange={vals => updateFileMeta(record.uid, { keywords: vals })} />
      ),
    },
    {
      title: '摘要', dataIndex: ['meta', 'abstract'], key: 'abstract', width: 180, ellipsis: true,
      render: (val: string, record: UploadFileEntry) => (
        <Input.TextArea value={val} size="small" rows={2}
          onChange={e => updateFileMeta(record.uid, { abstract: e.target.value })} />
      ),
    },
    // {
    //   title: '是否关联', dataIndex: 'isAssociated', key: 'isAssociated', width: 80,
    //   render: (val: boolean, record: UploadFileEntry) => (
    //     <Switch size="small" checked={val} onChange={() => {
    //       if (!val) openAssocForFile(record.uid);
    //       else updateFileEntry(record.uid, { isAssociated: false, associations: [] });
    //     }} />
    //   ),
    // },
    // {
    //   title: '关联信息', dataIndex: 'associations', key: 'associations', width: 100,
    //   render: (_: AssocRow[], record: UploadFileEntry) => (
    //     record.isAssociated ? (
    //       <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => openAssocForFile(record.uid)}>配置</Button>
    //     ) : <span style={{ color: '#bfbfbf' }}>未关联</span>
    //   ),
    // },
    {
      title: '同步向量库', dataIndex: 'syncChroma', key: 'syncChroma', width: 90,
      render: (val: boolean, record: UploadFileEntry) => (
        <Switch size="small" checked={val} onChange={v => updateFileEntry(record.uid, { syncChroma: v })} />
      ),
    },
    {
      title: '状态', key: 'status', width: 70,
      render: (_: any, record: UploadFileEntry) => {
        if (record.parsing) return <Spin size="small" />;
        if (record.parsed) return <Tag color="green">已解析</Tag>;
        return <Tag color="default">待解析</Tag>;
      },
    },
    // {
    //   title: '', key: 'extract', width: 120,
    //   render: (_: any, record: UploadFileEntry) => (
    //   // <Button
    //   //     type="link"
    //   //     size="small"
    //   //     icon={<ExperimentOutlined />}
    //   //     disabled={!record.parsed}
    //   //     onClick={() => openExtractModalForUploadEntry(record)}
    //   //   >
    //   //     自动提取方法
    //   //   </Button> 

    //     <span style={{ color: '#999' }}>已上传</span>
    //   ),
    // },
    {
      title: '', key: 'remove', width: 50,
      render: (_: any, record: UploadFileEntry) => (
        <Button type="link" danger size="small" onClick={() => handleRemoveFile(record.uid)}>删除</Button>
      ),
    },
  ];

  return (
    <div
      style={{
        height: '100%',
        minHeight: 0,
        boxSizing: 'border-box',
        padding: 0,
        background: '#f5f5f5',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <Card
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 12,
          overflow: 'hidden',
        }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            padding: 16,
          },
        }}
      >
        <div style={{ flexShrink: 0 }}>
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            type="card"
            items={tabItems}
          />
        </div>

        <div style={{ flex: 1, minHeight: 0, marginTop: 16, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <Card
            size="small"
            style={{
              flex: 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
            styles={{
              body: {
                flex: 1,
                minHeight: 0,
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
                padding: 12,
              },
            }}
            extra={
              <Space wrap>
                <Input placeholder="文件名关键词" value={filters.keyword} onChange={e => setFilters(p => ({ ...p, keyword: e.target.value }))} allowClear style={{ width: 160 }} />
                {isStandard && (
                  <Input
                    placeholder="标准号"
                    value={filters.stdCode}
                    onChange={e => setFilters(p => ({ ...p, stdCode: e.target.value }))}
                    allowClear
                    style={{ width: 140 }}
                  />
                )}
                <Input
                  placeholder="上传用户"
                  value={filters.uploadUser}
                  onChange={e => setFilters(p => ({ ...p, uploadUser: e.target.value }))}
                  allowClear
                  style={{ width: 120 }}
                />
                <DatePicker placeholder="上传日期起" value={filters.dateFrom ? dayjs(filters.dateFrom) : null} onChange={d => setFilters(p => ({ ...p, dateFrom: d ? d.format('YYYY-MM-DD') : '' }))} style={{ width: 130 }} />
                <DatePicker placeholder="上传日期止" value={filters.dateTo ? dayjs(filters.dateTo) : null} onChange={d => setFilters(p => ({ ...p, dateTo: d ? d.format('YYYY-MM-DD') : '' }))} style={{ width: 130 }} />
                {isOther ? (
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => {
                    setOtherAddFile(null);
                    otherAddForm.resetFields();
                    otherAddForm.setFieldsValue({
                      name: '',
                      abstract: '',
                      upload_user: getUploadUserLabelForSubmit(),
                      upload_time: dayjs(),
                    });
                    setOtherAddOpen(true);
                  }}>
                    新增
                  </Button>
                ) : (
                  <Button type="primary" icon={<CloudUploadOutlined />} onClick={openUploadModal}>
                    上传文件
                  </Button>
                )}
              </Space>
            }
          >
            <div ref={tableAreaRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <Table
                columns={mainColumns}
                dataSource={filteredFiles}
                rowKey="object_name"
                loading={loading}
                scroll={{ x: 1200, y: tableScrollY }}
                pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 条`, showSizeChanger: false }}
              />
            </div>
          </Card>
        </div>
      </Card>

      {/* Upload Modal */}
      <Modal
        title="上传文件"
        open={uploadModalOpen}
        onCancel={closeUploadModal}
        footer={null}
        width={1400}
        destroyOnHidden
      >
        <div style={{ marginBottom: 12 }}>
          <Upload.Dragger
            multiple
            beforeUpload={handleFileSelect}
            accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.wps,.et,.dps"
          >
            <p><CloudUploadOutlined /> 点击或拖拽文件到此区域（支持批量上传）</p>
          </Upload.Dragger>
        </div>

        {uploadFileList.length > 0 && (
          <>
            <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Space>
                <span>全部同步向量库：</span>
                <Switch checked={allSyncChroma} onChange={v => {
                  setAllSyncChroma(v);
                  setUploadFileList(prev => prev.map(e => ({ ...e, syncChroma: v })));
                }} />
                <span>重复文件：</span>
                <Select value={duplicateAction} onChange={setDuplicateAction} style={{ width: 100 }}>
                  <Option value="replace">替换</Option>
                  <Option value="skip">跳过</Option>
                  <Option value="check">询问</Option>
                </Select>
              </Space>
              <Space>
                <Button icon={<ScanOutlined />} onClick={handleAutoParse}
                  disabled={uploadFileList.every(e => e.parsed || e.parsing)}>
                  自动解析
                </Button>
              </Space>
            </div>

            <Table
              size="small"
              dataSource={uploadFileList}
              columns={metaColumns}
              rowKey="uid"
              pagination={false}
              scroll={{ y: 400 }}
            />

            <div style={{ textAlign: 'right', marginTop: 16 }}>
              <Space>
                <Button onClick={closeUploadModal}>取消</Button>
                <Button type="primary" onClick={handleUpload} loading={loading}
                  disabled={!uploadFileList.every(e => e.parsed)}>
                  确认上传
                </Button>
              </Space>
            </div>
          </>
        )}
      </Modal>

      {/* Association Config Modal */}
      <Modal
        title={`配置关联节点${assocTargetRowUid ? ` - ${uploadFileList.find(e => e.uid === assocTargetRowUid)?.name || ''}` : ''}`}
        open={assocModalOpen}
        onCancel={() => { setAssocModalOpen(false); setAssocTargetRowUid(null); setAssocRows([]); }}
        onOk={saveAssocForFile}
        width={700}
        destroyOnHidden
      >
        {renderAssocTable()}
      </Modal>

      {/* Edit Modal */}
      <Modal
        title="编辑文件"
        open={editModalOpen}
        onCancel={() => setEditModalOpen(false)}
        onOk={handleEditSave}
        width={600}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item label="文件名" name="title"><Input placeholder="文件标题" /></Form.Item>
          {isStandard && (
            <>
              <Form.Item label="标准号" name="std_code"><Input placeholder="标准号" /></Form.Item>
              <Form.Item label="文档ID" name="doc_id"><Input value={editingFile?.doc_id || ''} disabled /></Form.Item>
              {/* <Form.Item label="CCS" name="ccs"><Input placeholder="中国分类号" /></Form.Item> */}
            </>
          )}
          <Form.Item label="摘要" name="abstract"><Input.TextArea rows={3} placeholder="文件摘要" /></Form.Item>
          <Form.Item label="上传用户">
            <Input value={editingFile?.upload_userid || (typeof window !== 'undefined' ? (localStorage.getItem('userName') || '') : '')} disabled />
          </Form.Item>
          <Form.Item label="上传时间">
            <Input value={editingFile?.upload_time ? dayjs(editingFile.upload_time).format('YYYY-MM-DD') : ''} disabled />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新增文件"
        open={otherAddOpen}
        onCancel={() => { setOtherAddOpen(false); setOtherAddFile(null); }}
        onOk={handleOtherAddSubmit}
        confirmLoading={otherAddLoading}
        destroyOnHidden
        width={560}
      >
        <Form form={otherAddForm} layout="vertical">
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="文件名称" />
          </Form.Item>
          <Form.Item label="摘要" name="abstract">
            <Input.TextArea rows={3} placeholder="摘要（可选）" />
          </Form.Item>
          <Form.Item label="上传附件" required>
            <Upload
              maxCount={1}
              beforeUpload={(file) => {
                setOtherAddFile(file);
                return false;
              }}
              onRemove={() => setOtherAddFile(null)}
              fileList={otherAddFile ? [{ uid: '-1', name: otherAddFile.name, status: 'done' as const }] : []}
              accept=".doc,.docx,.ppt,.pptx,.xls,.xlsx,.wps,.pdf,.jpg,.jpeg,.png,.gif,.webp,.bmp,.tif,.tiff"
            >
              <Button icon={<CloudUploadOutlined />}>选择文件</Button>
            </Upload>
          </Form.Item>
          <Form.Item label="上传用户" name="upload_user">
            <Input disabled />
          </Form.Item>
          <Form.Item label="上传时间" name="upload_time" rules={[{ required: true, message: '请选择日期' }]}>
            <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`自动提取方法 - ${extractTargetFile?.title || extractUploadEntry?.name || ''}`}
        open={extractModalOpen}
        onCancel={() => { setExtractModalOpen(false); setExtractTargetFile(null); setExtractUploadEntry(null); setExtractToc([]); setExtractCheckedKeys([]); setExtractCheckedNodes([]); }}
        onOk={handleExtractSubmit}
        confirmLoading={extractSubmitting}
        width={700}
        zIndex={1050}
        getContainer={() => document.body}
        okText="自动提取方法"
        cancelText="取消"
        okButtonProps={{ disabled: extractCheckedKeys.length === 0 }}
      >
        <div style={{ marginBottom: 12 }}>
          <p style={{ color: '#666', fontSize: 13 }}>请勾选需要提取方法的章节：</p>
        </div>
        {extractTocLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : extractToc.length > 0 ? (
          <div style={{ maxHeight: 500, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 4, padding: 8 }}>
            <Tree
              checkable
              checkStrictly
              checkedKeys={extractCheckedKeys}
              onCheck={handleExtractCheck}
              treeData={extractToc}
              defaultExpandAll
              height={500}
            />
          </div>
        ) : (
          <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无目录信息</div>
        )}
        {extractCheckedKeys.length > 0 && (
          <div style={{ marginTop: 12, padding: 8, background: '#f5f5f5', borderRadius: 4 }}>
            <span style={{ fontSize: 13, color: '#666' }}>已选择 {extractCheckedKeys.length} 个章节（仅顶级节点会生成方法）</span>
          </div>
        )}
      </Modal>
    </div>
  );
}

export const dynamic = 'force-dynamic';
