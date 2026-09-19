'use client';

import React from 'react';
import { ColorPicker, Drawer, Form, Radio, Select, Typography } from 'antd';
import type { ZnwdAppearance, ZnwdBgTheme, ZnwdFontColor, ZnwdFontFamily, ZnwdFontSize, ZnwdLocale } from '../hooks/useZnwdAppearance';
import { ZNWD_FONT_COLOR_PRESETS } from '../hooks/useZnwdAppearance';

const { Text } = Typography;

interface ZnwdAppearanceSettingsProps {
  open: boolean;
  onClose: () => void;
  appearance: ZnwdAppearance;
  onChange: (patch: Partial<ZnwdAppearance>) => void;
  locale: ZnwdLocale;
}

const LABELS = {
  zh: {
    title: '页面样式',
    fontSize: '字体大小',
    fontFamily: '字体样式',
    fontColor: '字体颜色',
    locale: '界面语言',
    bgTheme: '页面背景',
    system: '系统默认',
    song: '宋体',
    hei: '黑体',
    mono: '等宽',
    light: '默认白',
    soft: '浅灰',
    dark: '深色',
    zhLang: '中文',
    enLang: 'English',
  },
  en: {
    title: 'Appearance',
    fontSize: 'Font size',
    fontFamily: 'Font family',
    fontColor: 'Font color',
    locale: 'Language',
    bgTheme: 'Background',
    system: 'System',
    song: 'Serif (Song)',
    hei: 'Sans (Hei)',
    mono: 'Monospace',
    light: 'Light',
    soft: 'Soft gray',
    dark: 'Dark',
    zhLang: '中文',
    enLang: 'English',
  },
};

export default function ZnwdAppearanceSettings({
  open,
  onClose,
  appearance,
  onChange,
  locale,
}: ZnwdAppearanceSettingsProps) {
  const t = LABELS[locale] || LABELS.zh;

  return (
    <Drawer title={t.title} open={open} onClose={onClose} width={360}>
      <Form layout="vertical">
        <Form.Item label={t.fontSize}>
          <Radio.Group
            value={appearance.fontSize}
            onChange={(e) => onChange({ fontSize: e.target.value as ZnwdFontSize })}
            optionType="button"
            options={[
              { label: '14', value: '14' },
              { label: '16', value: '16' },
              { label: '18', value: '18' },
            ]}
          />
        </Form.Item>
        <Form.Item label={t.fontFamily}>
          <Select
            value={appearance.fontFamily}
            onChange={(v: ZnwdFontFamily) => onChange({ fontFamily: v })}
            options={[
              { value: 'system', label: t.system },
              { value: 'song', label: t.song },
              { value: 'hei', label: t.hei },
              { value: 'mono', label: t.mono },
            ]}
          />
        </Form.Item>
        <Form.Item label={t.fontColor}>
          <Select
            value={appearance.fontColor}
            onChange={(v: ZnwdFontColor) => onChange({ fontColor: v })}
            options={ZNWD_FONT_COLOR_PRESETS.map((p) => ({
              value: p.value,
              label: locale === 'en' && p.value === 'auto' ? 'Auto' : p.label,
            }))}
          />
          {appearance.fontColor === 'custom' ? (
            <ColorPicker
              style={{ marginTop: 8 }}
              value={appearance.fontColorCustom}
              onChange={(_, hex) => onChange({ fontColorCustom: hex })}
              showText
            />
          ) : null}
        </Form.Item>
        <Form.Item label={t.locale}>
          <Radio.Group
            value={appearance.locale}
            onChange={(e) => onChange({ locale: e.target.value as ZnwdLocale })}
            options={[
              { label: t.zhLang, value: 'zh' },
              { label: t.enLang, value: 'en' },
            ]}
          />
        </Form.Item>
        <Form.Item label={t.bgTheme}>
          <Radio.Group
            value={appearance.bgTheme}
            onChange={(e) => onChange({ bgTheme: e.target.value as ZnwdBgTheme })}
            optionType="button"
            options={[
              { label: t.light, value: 'light' },
              { label: t.soft, value: 'soft' },
              { label: t.dark, value: 'dark' },
            ]}
          />
        </Form.Item>
      </Form>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {locale === 'en'
          ? 'Settings are saved in this browser.'
          : '设置将保存在当前浏览器中。'}
      </Text>
    </Drawer>
  );
}
