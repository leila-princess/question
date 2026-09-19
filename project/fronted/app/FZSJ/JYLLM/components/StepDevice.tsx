import { useState, useEffect, useMemo } from 'react';
import { Input, Button, Checkbox, Tag, Space, Spin, Empty } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { fetchEquipmentTree } from '../api';
import type { DeviceSelection } from '../types/flow';

interface StepDeviceProps {
  value: DeviceSelection;
  onChange: (v: DeviceSelection) => void;
  onNext: () => void;
  onPrev?: () => void;
}

export default function StepDevice({ value, onChange, onNext, onPrev }: StepDeviceProps) {
  const [treeData, setTreeData] = useState<any[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);

  useEffect(() => {
    (async () => {
      setTreeLoading(true);
      try {
        const data = await fetchEquipmentTree();
        setTreeData((data as any[]) || []);
      } catch { /* ignore */ }
      finally { setTreeLoading(false); }
    })();
  }, []);

  const flatDevices = useMemo(() => {
    const devices: { key: string; name: string; category: string; type: string }[] = [];
    // Backend returns: [{ title, key, children: [{ title, key, children: [{ title, key, isLeaf: true }] }] }]
    for (const cat of treeData) {
      const catName = cat.title || '';
      for (const t of cat.children || []) {
        const typeName = t.title || '';
        for (const eq of t.children || []) {
          if (eq.isLeaf) {
            devices.push({
              key: eq.key || '',
              name: eq.title || '',
              category: catName,
              type: typeName,
            });
          }
        }
      }
    }
    return devices;
  }, [treeData]);

  const filtered = useMemo(() => {
    if (!value.searchKey) return flatDevices;
    const kw = value.searchKey.toLowerCase();
    return flatDevices.filter(d =>
      d.name.toLowerCase().includes(kw) ||
      d.category.toLowerCase().includes(kw) ||
      d.type.toLowerCase().includes(kw)
    );
  }, [flatDevices, value.searchKey]);

  const toggleDevice = (key: string) => {
    const selected = value.selectedDevices.includes(key)
      ? value.selectedDevices.filter(k => k !== key)
      : [...value.selectedDevices, key];
    onChange({ ...value, selectedDevices: selected });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <Input
          placeholder="搜索设备类型或具体设备..."
          prefix={<SearchOutlined />}
          value={value.searchKey}
          onChange={e => onChange({ ...value, searchKey: e.target.value })}
          allowClear
          size="large"
        />
      </div>

      <div style={{ maxHeight: 300, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 8, padding: 8 }}>
        {treeLoading ? <Spin style={{ display: 'block', margin: '40px auto' }} /> :
          filtered.length === 0 ? <Empty description="未找到匹配的设备" image={Empty.PRESENTED_IMAGE_SIMPLE} /> :
          filtered.map(d => (
            <div
              key={d.key}
              onClick={() => toggleDevice(d.key)}
              style={{
                padding: '8px 12px',
                marginBottom: 4,
                borderRadius: 6,
                cursor: 'pointer',
                border: `1px solid ${value.selectedDevices.includes(d.key) ? '#1890ff' : '#e5e7eb'}`,
                background: value.selectedDevices.includes(d.key) ? '#e6f7ff' : '#fff',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <span style={{ fontSize: 13, fontWeight: 500 }}>{d.name}</span>
              <Space size={4}>
                {d.category && <Tag color="blue" style={{ fontSize: 10 }}>{d.category}</Tag>}
                {d.type && <Tag style={{ fontSize: 10 }}>{d.type}</Tag>}
                {value.selectedDevices.includes(d.key) && <span style={{ color: '#1890ff', fontSize: 12 }}>✓</span>}
              </Space>
            </div>
          ))
        }
      </div>

      <Input
        placeholder="请输入设备具体型号（可选）"
        value={value.modelInput}
        onChange={e => onChange({ ...value, modelInput: e.target.value })}
        size="middle"
      />

      {value.selectedDevices.length > 0 && (
        <Tag color="blue" style={{ fontSize: 12 }}>已选 {value.selectedDevices.length} 个设备</Tag>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
        <div>{onPrev && <Button onClick={onPrev}>上一步</Button>}</div>
        <Button type="primary" disabled={value.selectedDevices.length === 0} onClick={onNext}>
          下一步
        </Button>
      </div>
    </div>
  );
}
