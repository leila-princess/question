import { useMemo } from 'react';
import { Checkbox, Table, Tag, Empty, Spin, Button } from 'antd';
import type { ColumnsType } from 'antd/es/table';

interface StepMatrixProps {
  mode: 'project' | 'indicator';
  devices: { id: string; name: string }[];
  environments: string[];
  items: Record<string, any[]>;
  value: string[];
  onChange: (selected: string[]) => void;
  onNext: () => void;
  onPrev: () => void;
  loading?: boolean;
}

export default function StepMatrix({ mode, devices, environments, items, value, onChange, onNext, onPrev, loading }: StepMatrixProps) {
  const isMatrix = devices.length > 1 || environments.length > 1;

  const toggleItem = (itemId: string) => {
    const selected = value.includes(itemId)
      ? value.filter(id => id !== itemId)
      : [...value, itemId];
    onChange(selected);
  };

  const flatItems = useMemo(() => {
    const all: any[] = [];
    Object.values(items).forEach(arr => {
      arr.forEach(item => {
        const id = item.id || item.Id || item.name || item.Name;
        if (!all.find(a => (a.id || a.Id || a.name || a.Name) === id)) {
          all.push(item);
        }
      });
    });
    return all;
  }, [items]);

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>;

  if (!isMatrix) {
    const key = `${devices[0]?.id}_${environments[0]}`;
    const cellItems = items[key] || flatItems;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>
          选择{mode === 'project' ? '试验项目' : '考核指标'}
        </div>
        {cellItems.length === 0 ? <Empty description={`暂无相关${mode === 'project' ? '项目' : '指标'}`} /> : (
          <div style={{ maxHeight: 250, overflow: 'auto' }}>
            {cellItems.map(item => {
              const id = item.id || item.Id;
              const name = item.name || item.Name;
              return (
                <div
                  key={id}
                  onClick={() => toggleItem(id)}
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
                  {value.includes(id) && <span style={{ color: '#1890ff' }}>✓</span>}
                </div>
              );
            })}
          </div>
        )}
        {value.length > 0 && <Tag color="blue">已选 {value.length} 项</Tag>}
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <Button onClick={onPrev}>上一步</Button>
          <Button type="primary" disabled={value.length === 0} onClick={onNext}>下一步</Button>
        </div>
      </div>
    );
  }

  const columns: ColumnsType<any> = [
    {
      title: '设备',
      dataIndex: 'deviceName',
      key: 'deviceName',
      width: 120,
      fixed: 'left' as const,
    },
    ...environments.map(env => ({
      title: env,
      dataIndex: `env_${env}`,
      key: `env_${env}`,
      render: (cellItems: any[]) => (
        <div style={{ maxHeight: 150, overflow: 'auto' }}>
          {cellItems?.map(item => {
            const id = item.id || item.Id;
            const name = item.name || item.Name;
            return (
              <Checkbox
                key={id}
                checked={value.includes(id)}
                onChange={() => toggleItem(id)}
                style={{ display: 'block', fontSize: 12, marginBottom: 2 }}
              >
                {name}
              </Checkbox>
            );
          })}
        </div>
      ),
    })),
  ];

  const dataSource = devices.map(dev => {
    const row: any = { key: dev.id, deviceName: dev.name };
    environments.forEach(env => {
      const key = `${dev.id}_${env}`;
      row[`env_${env}`] = items[key] || [];
    });
    return row;
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 500 }}>
        选择{mode === 'project' ? '试验项目' : '考核指标'}（设备×环境矩阵）
      </div>
      <Table
        columns={columns}
        dataSource={dataSource}
        size="small"
        pagination={false}
        scroll={{ x: 'max-content' }}
      />
      {value.length > 0 && <Tag color="blue">已选 {value.length} 项</Tag>}
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <Button onClick={onPrev}>上一步</Button>
        <Button type="primary" disabled={value.length === 0} onClick={onNext}>下一步</Button>
      </div>
    </div>
  );
}
