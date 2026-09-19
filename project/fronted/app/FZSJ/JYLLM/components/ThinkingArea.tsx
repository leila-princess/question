import { useState } from 'react';
import { Collapse, Tag, Spin } from 'antd';
import { BulbOutlined } from '@ant-design/icons';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import type { ThinkingEntry } from '../types/flow';

interface ThinkingAreaProps {
  entries: ThinkingEntry[];
  isStreaming: boolean;
}

export default function ThinkingArea({ entries, isStreaming }: ThinkingAreaProps) {
  const [collapsed, setCollapsed] = useState(true);

  if (!entries.length && !isStreaming) return null;

  const thinkingText = entries
    .map(e => e.content.replace(/▌$/, ''))
    .filter(Boolean)
    .join('');

  if (!thinkingText && !isStreaming) return null;

  return (
    <div style={{ marginBottom: 12 }}>
      <Collapse
        size="small"
        ghost
        activeKey={collapsed ? [] : ['thinking']}
        onChange={(keys) => setCollapsed(keys.length === 0)}
        style={{ background: '#fafafa', border: '1px solid #e5e7eb', borderRadius: 6 }}
        items={[{
          key: 'thinking',
          label: (
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              <BulbOutlined style={{ marginRight: 4 }} />
              思考过程
              {isStreaming && <Spin size="small" style={{ marginLeft: 8 }} />}
            </span>
          ),
          children: (
            <div style={{ maxHeight: 300, overflow: 'auto', padding: '0 8px' }}>
              <div style={{ fontSize: 12, color: '#8c8c8c', lineHeight: 1.6 }}>
                <MarkdownRenderer content={thinkingText} />
                {isStreaming && <span style={{ animation: 'blink 1s infinite' }}>▌</span>}
              </div>
            </div>
          ),
        }]}
      />
    </div>
  );
}
