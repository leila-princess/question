'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  DEFAULT_APPEARANCE,
  ZNWD_BG_THEME_KEY,
  ZNWD_FONT_COLOR_CUSTOM_KEY,
  ZNWD_FONT_COLOR_KEY,
  ZNWD_FONT_FAMILY_KEY,
  ZNWD_FONT_SIZE_KEY,
  ZNWD_LOCALE_KEY,
  type ZnwdAppearance,
  type ZnwdBgTheme,
  type ZnwdFontColor,
  type ZnwdFontFamily,
  type ZnwdFontSize,
  type ZnwdLocale,
} from './useZnwdAppearance';

export function useZnwdAppearanceState() {
  const [appearance, setAppearance] = useState<ZnwdAppearance>(DEFAULT_APPEARANCE);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const fs = (localStorage.getItem(ZNWD_FONT_SIZE_KEY) || '16') as ZnwdFontSize;
    const ff = (localStorage.getItem(ZNWD_FONT_FAMILY_KEY) || 'system') as ZnwdFontFamily;
    const fc = (localStorage.getItem(ZNWD_FONT_COLOR_KEY) || 'auto') as ZnwdFontColor;
    const fcc = localStorage.getItem(ZNWD_FONT_COLOR_CUSTOM_KEY) || '#1f1f1f';
    const loc = (localStorage.getItem(ZNWD_LOCALE_KEY) || 'zh') as ZnwdLocale;
    const bg = (localStorage.getItem(ZNWD_BG_THEME_KEY) || 'light') as ZnwdBgTheme;
    setAppearance({ fontSize: fs, fontFamily: ff, fontColor: fc, fontColorCustom: fcc, locale: loc, bgTheme: bg });
  }, []);

  const updateAppearance = useCallback((patch: Partial<ZnwdAppearance>) => {
    setAppearance((prev) => {
      const next = { ...prev, ...patch };
      if (typeof window !== 'undefined') {
        localStorage.setItem(ZNWD_FONT_SIZE_KEY, next.fontSize);
        localStorage.setItem(ZNWD_FONT_FAMILY_KEY, next.fontFamily);
        localStorage.setItem(ZNWD_FONT_COLOR_KEY, next.fontColor);
        localStorage.setItem(ZNWD_FONT_COLOR_CUSTOM_KEY, next.fontColorCustom);
        localStorage.setItem(ZNWD_LOCALE_KEY, next.locale);
        localStorage.setItem(ZNWD_BG_THEME_KEY, next.bgTheme);
      }
      return next;
    });
  }, []);

  return { appearance, updateAppearance };
}
