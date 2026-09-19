'use client';

import React, { useState, useEffect, useCallback, useRef, useLayoutEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  Card, Tabs, Table, Button, Space, Input, Modal, Form,
  Select, Tag, message, Upload, Popconfirm,
  Spin, DatePicker, Tree, Checkbox, Switch
} from 'antd';
import {
  FolderOutlined, PlusOutlined, EditOutlined,
  DeleteOutlined, CloudUploadOutlined, ReadOutlined,
  ExperimentOutlined, AuditOutlined
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import UploadModal from './components/UploadModal';
import MathAwareTextArea from '@/components/MathAwareTextArea';
import { pollDocumentToc, fetchJsonSafe, buildTocTreeNodes, fetchSavedDocumentToc } from './components/documentTocUtils';
import { useTablePagination } from '@/utils/useTablePagination';
import { TAB_CATEGORIES } from './constants/categories';
import { resolveSchemeSourceDisplay } from './constants/schemeSourceLabels';
import { wjglUploadUrl } from './utils/wjglApi';

const { Option } = Select;

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
  std_name: string;
  publish_date: string;
  issuer: string;
  review_status: string;
  upload_userid: string;
  upload_time: string;
  file_size: number;
  abstract: string;
  keywords: string;
}

function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
  parsed: boolean;
  parsing: boolean;
}

export default function WJGLPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState('standard');
  const [files, setFiles] = useState<MinIOFile[]>([]);
  const [treeData, setTreeData] = useState<DataNode[]>([]);
  const [selectedTreeKey, setSelectedTreeKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [uploadModalOpen, setUploadModalOpen] = useState(false);

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
  const [extractUseLlmMerge, setExtractUseLlmMerge] = useState(false);
  const [extractTocProgress, setExtractTocProgress] = useState('');

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
  const isScheme = activeTab === 'scheme';

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

  const openUploadModal = () => setUploadModalOpen(true);
  const closeUploadModal = () => setUploadModalOpen(false);

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
      const res = await fetch(wjglUploadUrl('/simple-upload'), { method: 'POST', body: fd });
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
    setExtractTocProgress('正在加载文档目录…');
    try {
      let toc = await fetchSavedDocumentToc({
        objectName: record.object_name,
        docId: record.doc_id,
        category: record.category || category,
      });
      if (!toc.length) {
        setExtractTocProgress('正在获取文档目录…');
        toc = await pollDocumentToc({
          objectName: record.object_name,
          docId: record.doc_id,
          category: record.category || category,
          onProgress: setExtractTocProgress,
        });
      }
      const treeNodes = buildTocTreeNodes(toc);
      setExtractToc(treeNodes);
      if (!treeNodes.length) {
        message.warning('未识别到章节目录，请确认文档已解析完成');
      }
    } catch (e: any) {
      message.error(`获取目录失败: ${e?.message || '网络错误'}`);
      setExtractToc([]);
    } finally {
      setExtractTocProgress('');
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
    setExtractTocProgress('正在获取文档目录…');
    try {
      const formData = new FormData();
      formData.append('file', entry.file);
      const res = await fetch(wjglUploadUrl('/document-toc-upload'), { method: 'POST', body: formData });
      const data = await fetchJsonSafe(res);
      if (data.success && data.data?.toc?.length) {
        const treeNodes = buildTocTreeNodes(data.data.toc);
        setExtractToc(treeNodes);
      } else {
        message.warning(data.error || data.data?.message || '获取目录失败');
        setExtractToc([]);
      }
    } catch (e: any) {
      message.error(`获取目录失败: ${e?.message || '网络错误'}`);
      setExtractToc([]);
    } finally {
      setExtractTocProgress('');
      setExtractTocLoading(false);
    }
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
    const hideLoading = message.loading(
      extractUseLlmMerge ? '正在提取并 LLM 整合方法，请稍候…' : '正在提取方法（已优先使用解析缓存）…',
      0,
    );
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
        formData.append('use_llm_merge', extractUseLlmMerge ? 'true' : 'false');
        const res = await fetch('/api/FLGL_method/method-extract-sections-file', {
          method: 'POST',
          body: formData,
        });
        const data = await fetchJsonSafe(res);
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
          use_llm_merge: extractUseLlmMerge,
        };
        const res = await fetch('/api/FLGL_method/method-extract-sections', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        });
        const data = await fetchJsonSafe(res);
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
      hideLoading();
      setExtractSubmitting(false);
    }
  };

  const openReviewPage = (record: MinIOFile) => {
    router.push(`/ZSGL/WJGL/files/${record.doc_id}?category=${encodeURIComponent(record.category || category)}`);
  };

  const standardColumns: ColumnsType<MinIOFile> = [
    {
      title: '标题',
      key: 'std_name',
      ellipsis: true,
      width: 220,
      render: (_: unknown, record: MinIOFile) => record.std_name || record.title || '-',
    },
    {
      title: '文档标识',
      key: 'std_code',
      width: 140,
      render: (_: unknown, record: MinIOFile) => (
        record.std_code
          ? <span>{record.std_code}</span>
          : <span style={{ color: '#999' }}>{record.doc_id || '-'}</span>
      ),
    },
    {
      title: '颁布时间',
      dataIndex: 'publish_date',
      key: 'publish_date',
      width: 110,
      render: (val: string) => val ? dayjs(val).format('YYYY-MM-DD') : '-',
    },
    {
      title: '单位',
      dataIndex: 'issuer',
      key: 'issuer',
      ellipsis: true,
      width: 160,
    },
    {
      title: '摘要',
      dataIndex: 'abstract',
      key: 'abstract',
      ellipsis: true,
      width: 200,
    },
    {
      title: '文件信息',
      key: 'file_info',
      width: 200,
      render: (_: unknown, record: MinIOFile) => (
        <Space size={[4, 4]} wrap>
          <Tag>{(record.file_type || 'file').toUpperCase()}</Tag>
          <Tag>{formatFileSize(record.file_size)}</Tag>
          {record.review_status === 'draft' && <Tag color="orange">待审核</Tag>}
          {record.review_status === 'confirmed' && <Tag color="green">已确认</Tag>}
        </Space>
      ),
    },
  ];

  const defaultColumns: ColumnsType<MinIOFile> = [
    { title: '文件名', dataIndex: 'title', key: 'title', ellipsis: true, width: 280 },
    ...(isScheme
      ? [{
          title: '来源',
          key: 'source',
          width: 100,
          render: (_: unknown, record: MinIOFile) => {
            const src = resolveSchemeSourceDisplay(record.file_label);
            return <Tag color={src.color}>{src.label}</Tag>;
          },
        } as ColumnsType<MinIOFile>[number]]
      : []),
    { title: '摘要', dataIndex: 'abstract', key: 'abstract', ellipsis: true, width: 200 },
    { title: '上传用户', dataIndex: 'upload_userid', key: 'upload_userid', width: 150 },
    {
      title: '上传时间', dataIndex: 'upload_time', key: 'upload_time', width: 180,
      render: (val: string) => {
        if (!val) return '-';
        try { return dayjs(val).format('YYYY-MM-DD'); } catch { return val; }
      },
    },
  ];

  const mainColumns: ColumnsType<MinIOFile> = [
    ...(isStandard ? standardColumns : defaultColumns),
    {
      title: '操作', key: 'action', width: 280, fixed: 'right' as const,
      render: (_: any, record: MinIOFile) => (
        <Space wrap>
          <Button type="link" size="small" icon={<AuditOutlined />} onClick={() => openReviewPage(record)}>
            审核
          </Button>
          <Button
            type="link"
            size="small"
            icon={<ReadOutlined />}
            onClick={() => router.push(`/Standards?object_name=${encodeURIComponent(record.object_name)}`)}
          >
            阅读
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Button type="link" size="small" icon={<ExperimentOutlined />} onClick={() => openExtractModal(record)}>提取方法</Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const filteredFiles = files;

  const tablePagination = useTablePagination(filteredFiles.length, {
    resetDeps: [activeTab, filters],
  });

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
                pagination={tablePagination}
              />
            </div>
          </Card>
        </div>
      </Card>

      <UploadModal
        open={uploadModalOpen}
        onClose={closeUploadModal}
        category={category}
        onComplete={() => {
          fetchFiles();
          fetchTree();
          void fetchCategoryCounts();
        }}
      />

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
          <Form.Item label="摘要" name="abstract"><MathAwareTextArea rows={3} placeholder="文件摘要" /></Form.Item>
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
            <MathAwareTextArea rows={3} placeholder="摘要（可选）" />
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
        onCancel={() => { setExtractModalOpen(false); setExtractTargetFile(null); setExtractUploadEntry(null); setExtractToc([]); setExtractCheckedKeys([]); setExtractCheckedNodes([]); setExtractUseLlmMerge(false); }}
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
            <Switch checked={extractUseLlmMerge} onChange={setExtractUseLlmMerge} />
            <span style={{ fontSize: 13, color: '#333' }}>是否需要 LLM 整合</span>
            <span style={{ fontSize: 12, color: '#999' }}>（整合为试验条件 / 试验过程 / 结果评定，并重编号标题、溯源「见XXX」）</span>
          </div>
        </div>
        {extractTocLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
            {extractTocProgress ? (
              <p style={{ marginTop: 12, color: '#666', fontSize: 13 }}>{extractTocProgress}</p>
            ) : null}
          </div>
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
