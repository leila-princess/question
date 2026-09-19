import { useState } from 'react';
import { Button, Space, Tooltip } from 'antd';
import { ClearOutlined, StopOutlined, SettingOutlined } from '@ant-design/icons';
import { Sender } from '@ant-design/x';

interface ChatInputProps {
  onSend: (message: string) => void;
  onClear?: () => void;
  onStop?: () => void;
  onConfig?: () => void;
  disabled?: boolean;
  placeholder?: string;
  isStreaming?: boolean;
}

export default function ChatInput({
  onSend,
  onClear,
  onStop,
  onConfig,
  disabled = false,
  placeholder = '输入消息...',
  isStreaming = false,
}: ChatInputProps) {
  const [value, setValue] = useState('');

  const handleSend = () => {
    if (value.trim() && !disabled) {
      onSend(value.trim());
      setValue('');
    }
  };

  return (
    <div style={{
      background: '#fff',
      borderTop: '1px solid #e5e7eb',
      padding: '12px 16px',
      display: 'flex',
      gap: 8,
      alignItems: 'flex-end',
    }}>
      {onConfig && (
        <Tooltip title="配置试验参数">
          <Button
            type="primary"
            icon={<SettingOutlined />}
            onClick={onConfig}
            style={{ flexShrink: 0 }}
          >
            配置
          </Button>
        </Tooltip>
      )}
      <div style={{ flex: 1 }}>
        <Sender
          value={value}
          onChange={setValue}
          onSubmit={handleSend}
          placeholder={placeholder}
          disabled={disabled || isStreaming}
          loading={isStreaming}
          style={{
            borderRadius: 8,
            boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
          }}
        />
      </div>
      <Space>
        {isStreaming && (
          <Tooltip title="停止生成">
            <Button
              type="primary"
              danger
              icon={<StopOutlined />}
              onClick={onStop}
            />
          </Tooltip>
        )}
        {onClear && (
          <Tooltip title="清空对话">
            <Button
              icon={<ClearOutlined />}
              onClick={onClear}
            />
          </Tooltip>
        )}
      </Space>
    </div>
  );
}
