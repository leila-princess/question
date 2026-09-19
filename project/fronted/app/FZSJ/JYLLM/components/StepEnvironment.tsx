import { useState, useEffect } from 'react';
import { Button, Input, Spin, Alert, Space, Tag, Collapse } from 'antd';
import { CheckCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { callLibrarian, fetchEnvironments } from '../api';
import type { EnvironmentSelection } from '../types/flow';

const { TextArea } = Input;

const ENV_COLORS: Record<string, string> = {
  "HJ_0001": "#ef4444",
  "HJ_0002": "#3b82f6",
  "HJ_0003": "#10b981",
  "HJ_0004": "#6366f1",
  "HJ_0005": "#f59e0b",
  "HJ_0006": "#d97706",
  "HJ_0007": "#0ea5e9",
  "HJ_0008": "#8b5cf6",
};

interface EnvItem {
  id: string;
  name: string;
  label: string;
}

interface StepEnvironmentProps {
  value: EnvironmentSelection;
  onChange: (v: EnvironmentSelection) => void;
  onNext: () => void;
  onPrev: () => void;
  selectedDevices: string[];
}

export default function StepEnvironment({ value, onChange, onNext, onPrev, selectedDevices }: StepEnvironmentProps) {
  const [loading, setLoading] = useState(false);
  const [loadingEnvs, setLoadingEnvs] = useState<Record<string, boolean>>({});
  const [error, setError] = useState('');
  const [envSuggestions, setEnvSuggestions] = useState<Record<string, string>>({});
  const [librarianCalled, setLibrarianCalled] = useState(false);
  const [envList, setEnvList] = useState<EnvItem[]>([]);
  const [envLoading, setEnvLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const data = await fetchEnvironments();
        setEnvList((data as any[]) || []);
      } catch { /* ignore */ }
      finally { setEnvLoading(false); }
    })();
  }, []);

  const toggleEnv = (envId: string) => {
    const selected = value.selectedEnvs.includes(envId)
      ? value.selectedEnvs.filter(e => e !== envId)
      : [...value.selectedEnvs, envId];
    const newVal = { ...value, selectedEnvs: selected };
    onChange(newVal);
    setLibrarianCalled(false);
  };

  const getEnvName = (envId: string) => envList.find(e => e.id === envId)?.name || envId;
  const getEnvColor = (envId: string) => ENV_COLORS[envId] || '#8b5cf6';

  const requestLibrarianForEnv = async (envId: string) => {
    if (!selectedDevices.length) return;
    setLoadingEnvs(prev => ({ ...prev, [envId]: true }));
    try {
      const result = await callLibrarian({
        equipment_ids: selectedDevices,
        environment_id: envId,
      });
      if (result && (result as any).suggestion) {
        setEnvSuggestions(prev => ({ ...prev, [envId]: (result as any).suggestion }));
      }
    } catch (e: any) {
      setError(e.message || `资料员调用失败 (${getEnvName(envId)})`);
    } finally {
      setLoadingEnvs(prev => ({ ...prev, [envId]: false }));
    }
  };

  const requestAllLibrarians = async () => {
    if (!value.selectedEnvs.length || !selectedDevices.length) return;
    setLoading(true);
    setError('');
    const suggestions: Record<string, string> = {};
    for (const envId of value.selectedEnvs) {
      try {
        const result = await callLibrarian({
          equipment_ids: selectedDevices,
          environment_id: envId,
        });
        if (result && (result as any).suggestion) {
          suggestions[envId] = (result as any).suggestion;
        }
      } catch (e: any) {
        setError(prev => prev + `\n${getEnvName(envId)}: ${e.message || '调用失败'}`);
      }
    }
    setEnvSuggestions(suggestions);
    setLoading(false);
    setLibrarianCalled(true);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>选择环境条件</div>
        {envLoading ? <Spin /> : (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {envList.map(env => (
              <div
                key={env.id}
                onClick={() => toggleEnv(env.id)}
                style={{
                  padding: '12px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  border: `2px solid ${value.selectedEnvs.includes(env.id) ? getEnvColor(env.id) : '#e5e7eb'}`,
                  background: value.selectedEnvs.includes(env.id) ? `${getEnvColor(env.id)}10` : '#fff',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 500 }}>{env.name}</span>
                {value.selectedEnvs.includes(env.id) && <CheckCircleOutlined style={{ color: getEnvColor(env.id) }} />}
              </div>
            ))}
          </div>
        )}
      </div>

      {value.selectedEnvs.length > 0 && selectedDevices.length > 0 && !librarianCalled && (
        <Button onClick={requestAllLibrarians} loading={loading} block>
          调用资料员获取所有环境参考答案 ({value.selectedEnvs.length}个)
        </Button>
      )}

      {loading && (
        <div style={{ textAlign: 'center', padding: 16 }}>
          <Spin indicator={<LoadingOutlined style={{ fontSize: 20 }} spin />} />
          <div style={{ marginTop: 8, color: '#8c8c8c', fontSize: 12 }}>资料员分析中...</div>
        </div>
      )}

      {value.selectedEnvs.length > 0 && librarianCalled && (
        <Collapse
          size="small"
          items={value.selectedEnvs.map(envId => ({
            key: envId,
            label: (
              <Space>
                <Tag color={getEnvColor(envId)}>{getEnvName(envId)}</Tag>
                {loadingEnvs[envId] && <Spin size="small" />}
                {envSuggestions[envId] && <Tag color="success">已获取</Tag>}
                {!envSuggestions[envId] && !loadingEnvs[envId] && <Tag>未获取</Tag>}
              </Space>
            ),
            children: (
              <div>
                {envSuggestions[envId] ? (
                  <>
                    <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                      {envSuggestions[envId]}
                    </div>
                    <Button
                      size="small"
                      type="primary"
                      onClick={() => {
                        const current = value.envConfig ? value.envConfig + '\n\n' : '';
                        onChange({ ...value, envConfig: current + `[${getEnvName(envId)}]\n${envSuggestions[envId]}` });
                      }}
                    >
                      采纳此环境答案
                    </Button>
                  </>
                ) : (
                  <Button
                    size="small"
                    loading={loadingEnvs[envId]}
                    onClick={() => requestLibrarianForEnv(envId)}
                  >
                    获取参考答案
                  </Button>
                )}
              </div>
            ),
          }))}
        />
      )}

      {error && <Alert message={error} type="error" showIcon />}

      <TextArea
        rows={4}
        placeholder="输入具体环境设置（可采纳上方参考答案或手动输入，支持多环境组合）"
        value={value.envConfig}
        onChange={e => onChange({ ...value, envConfig: e.target.value })}
      />

      {value.selectedEnvs.length > 0 && (
        <Space wrap>
          {value.selectedEnvs.map(envId => (
            <Tag key={envId} color={getEnvColor(envId)}>{getEnvName(envId)}</Tag>
          ))}
        </Space>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
        <Button onClick={onPrev}>上一步</Button>
        <Button type="primary" disabled={value.selectedEnvs.length === 0} onClick={onNext}>
          下一步
        </Button>
      </div>
    </div>
  );
}
