import { useState, useEffect, useMemo } from 'react';
import { Input, Tag, Empty, Spin, Button, Upload, message, List, Typography, Card } from 'antd';
import { SearchOutlined, InboxOutlined, FileTextOutlined, CheckCircleOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { fetchMethods, uploadFile, parseFile } from '../api';

import { Attachments } from '@ant-design/x';

const { Text } = Typography;

export interface UploadedFile {
  id: string;
  name: string;
  status: 'uploading' | 'parsing' | 'done' | 'error';
  metadata?: {
    title?: string;
    abstract?: string;
    keywords?: string[];
  };
}

interface StepMethodProps {
  selectedProjects: string[];
  selectedIndicators: string[];
  value: string[];
  onChange: (selected: string[]) => void;
  uploadedFiles: UploadedFile[];
  onUploadedFilesChange: React.Dispatch<React.SetStateAction<UploadedFile[]>>;
  selectedUploadedFiles: string[];
  onSelectedUploadedFilesChange: React.Dispatch<React.SetStateAction<string[]>>;
  onNext: () => void;
  onPrev: () => void;
}

export default function StepMethod({ 
  selectedProjects, 
  selectedIndicators, 
  value, 
  onChange, 
  uploadedFiles,
  onUploadedFilesChange,
  selectedUploadedFiles,
  onSelectedUploadedFilesChange,
  onNext, 
  onPrev 
}: StepMethodProps) {
  const [methods, setMethods] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!selectedProjects.length && !selectedIndicators.length) {
      setMethods([]);
      return;
    }
    (async () => {
      setLoading(true);
      try {
        const data = await fetchMethods({
          projectIds: selectedProjects,
          indicatorIds: selectedIndicators,
        });
        setMethods((data as any[]) || []);
      } catch { /* ignore */ }
      finally { setLoading(false); }
    })();
  }, [selectedProjects, selectedIndicators]);

  const filtered = useMemo(() => {
    if (!search) return methods;
    const kw = search.toLowerCase();
    return methods.filter(m =>
      (m.name || m.Name || '').toLowerCase().includes(kw) ||
      (m.description || '').toLowerCase().includes(kw)
    );
  }, [methods, search]);

  const toggleMethod = (id: string) => {
    const selected = value.includes(id)
      ? value.filter(v => v !== id)
      : [...value, id];
    onChange(selected);
  };

  const toggleFile = (id: string) => {
    const selected = selectedUploadedFiles.includes(id)
      ? selectedUploadedFiles.filter(v => v !== id)
      : [...selectedUploadedFiles, id];
    onSelectedUploadedFilesChange(selected);
  };

  const uploadProps: any = {
    name: 'file',
    multiple: true,
    accept: '.pdf,.docx',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }: any) => {
      const f = file as File;
      const tempId = `temp_${Date.now()}_${f.name}`;
      
      const newFile: UploadedFile = { id: tempId, name: f.name, status: 'uploading' };
      onUploadedFilesChange([...uploadedFiles, newFile]);

      try {
        const uploadResult = await uploadFile(f);
        const fileId = uploadResult.id || uploadResult.file_id || tempId;
        
        onUploadedFilesChange(prev => prev.map(p => 
          p.id === tempId ? { ...p, id: fileId, status: 'parsing' } : p
        ));

        const parseResult = await parseFile(fileId);
        
        onUploadedFilesChange(prev => prev.map(p => 
          p.id === fileId ? { 
            ...p, 
            status: 'done',
            metadata: {
              title: parseResult.title,
              abstract: parseResult.abstract,
              keywords: parseResult.keywords || [],
            }
          } : p
        ));
        
        onSelectedUploadedFilesChange([...selectedUploadedFiles, fileId]);
        
        onSuccess?.(parseResult);
        message.success(`${f.name} 上传并解析成功`);
      } catch (error: any) {
        console.error('Upload/Parse error:', error);
        onUploadedFilesChange(prev => prev.map(p => 
          p.id === tempId ? { ...p, status: 'error' } : p
        ));
        onError?.(error);
        message.error(`${f.name} 处理失败: ${error.message}`);
      }
    },
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div>
        <div style={{ marginBottom: 12, fontWeight: 500 }}>1. 选择试验方法</div>
        <Input
          placeholder="搜索试验方法..."
          prefix={<SearchOutlined />}
          value={search}
          onChange={e => setSearch(e.target.value)}
          allowClear
          style={{ marginBottom: 12 }}
        />

        <div style={{ maxHeight: 250, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 8, padding: 8 }}>
          {loading ? <Spin style={{ display: 'block', margin: '40px auto' }} /> :
            filtered.length === 0 ? <Empty description="暂无相关方法" image={Empty.PRESENTED_IMAGE_SIMPLE} /> :
            filtered.map(m => {
              const id = m.id || m.Id;
              const name = m.name || m.Name;
              return (
                <div
                  key={id}
                  onClick={() => toggleMethod(id)}
                  style={{
                    padding: '8px 12px',
                    marginBottom: 4,
                    borderRadius: 6,
                    cursor: 'pointer',
                    border: `1px solid ${value.includes(id) ? '#1890ff' : '#e5e7eb'}`,
                    background: value.includes(id) ? '#e6f7ff' : '#fff',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <span style={{ fontSize: 13 }}>{name}</span>
                  <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    {m.files?.length > 0 && <Tag color="purple" style={{ fontSize: 10 }}>{m.files.length}文件</Tag>}
                    {value.includes(id) && <span style={{ color: '#1890ff' }}>✓</span>}
                  </div>
                </div>
              );
            })
          }
        </div>
        {value.length > 0 && <Tag color="blue" style={{ marginTop: 8 }}>已选 {value.length} 个方法</Tag>}
      </div>


      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
        <Button onClick={onPrev}>上一步</Button>
        <Button type="primary" disabled={value.length === 0} onClick={onNext}>下一步</Button>
      </div>
    </div>
  );
}
