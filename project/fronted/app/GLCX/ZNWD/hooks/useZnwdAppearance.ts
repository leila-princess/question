import type { CSSProperties } from 'react';

export const ZNWD_FONT_SIZE_KEY = 'znwd_font_size';
export const ZNWD_FONT_FAMILY_KEY = 'znwd_font_family';
export const ZNWD_FONT_COLOR_KEY = 'znwd_font_color';
export const ZNWD_FONT_COLOR_CUSTOM_KEY = 'znwd_font_color_custom';
export const ZNWD_LOCALE_KEY = 'znwd_locale';
export const ZNWD_BG_THEME_KEY = 'znwd_bg_theme';

export type ZnwdFontSize = '14' | '16' | '18';
export type ZnwdFontFamily = 'system' | 'song' | 'hei' | 'mono';
export type ZnwdFontColor = 'auto' | '#1f1f1f' | '#1677ff' | '#389e0d' | '#cf1322' | 'custom';
export type ZnwdLocale = 'zh' | 'en';
export type ZnwdBgTheme = 'light' | 'soft' | 'dark';

export interface ZnwdAppearance {
  fontSize: ZnwdFontSize;
  fontFamily: ZnwdFontFamily;
  fontColor: ZnwdFontColor;
  fontColorCustom: string;
  locale: ZnwdLocale;
  bgTheme: ZnwdBgTheme;
}

export const DEFAULT_APPEARANCE: ZnwdAppearance = {
  fontSize: '16',
  fontFamily: 'system',
  fontColor: 'auto',
  fontColorCustom: '#1f1f1f',
  locale: 'zh',
  bgTheme: 'light',
};

export const ZNWD_FONT_COLOR_PRESETS: { value: ZnwdFontColor; label: string }[] = [
  { value: 'auto', label: '跟随主题' },
  { value: '#1f1f1f', label: '深灰' },
  { value: '#1677ff', label: '蓝色' },
  { value: '#389e0d', label: '绿色' },
  { value: '#cf1322', label: '红色' },
  { value: 'custom', label: '自定义' },
];

function resolveTextColor(a: ZnwdAppearance): string {
  if (a.fontColor === 'custom') {
    return a.fontColorCustom || '#1f1f1f';
  }
  if (a.fontColor !== 'auto') {
    return a.fontColor;
  }
  return a.bgTheme === 'dark' ? '#e8e8e8' : '#1f1f1f';
}

export function appearanceToCssVars(a: ZnwdAppearance): CSSProperties {
  const families: Record<ZnwdFontFamily, string> = {
    system: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    song: '"SimSun", "Songti SC", serif',
    hei: '"SimHei", "Heiti SC", "Microsoft YaHei", sans-serif',
    mono: '"Consolas", "Monaco", "Courier New", monospace',
  };
  const bg: Record<ZnwdBgTheme, { page: string; footer: string; bubble: string }> = {
    light: { page: '#ffffff', footer: '#fafafa', bubble: '#ffffff' },
    soft: { page: '#f5f7fa', footer: '#eef1f6', bubble: '#ffffff' },
    dark: { page: '#141414', footer: '#1f1f1f', bubble: '#262626' },
  };
  const theme = bg[a.bgTheme];
  const textColor = resolveTextColor(a);
  return {
    ['--znwd-font-size' as string]: `${a.fontSize}px`,
    ['--znwd-font-family' as string]: families[a.fontFamily],
    ['--znwd-page-bg' as string]: theme.page,
    ['--znwd-footer-bg' as string]: theme.footer,
    ['--znwd-bubble-bg' as string]: theme.bubble,
    ['--znwd-text-color' as string]: textColor,
    fontSize: `${a.fontSize}px`,
    fontFamily: families[a.fontFamily],
    color: textColor,
    background: theme.page,
  };
}
