import { useEffect, useRef, useState } from 'react';

/**
 * 在流式阶段将「已到达全文」与「当前展示长度」解耦，用短间隔追赶目标长度，形成打字机观感。
 */
export function useTypewriterReveal(
  fullText: string,
  active: boolean,
  opts?: { maxStep?: number; intervalMs?: number },
): string {
  const maxStep = opts?.maxStep ?? 48;
  const intervalMs = opts?.intervalMs ?? 18;
  const [shownLen, setShownLen] = useState(0);
  const targetRef = useRef(0);
  const prevActiveRef = useRef(false);
  targetRef.current = fullText.length;

  useEffect(() => {
    if (active && !prevActiveRef.current) {
      setShownLen(0);
    }
    prevActiveRef.current = active;
  }, [active]);

  useEffect(() => {
    if (!active) {
      return;
    }
    const id = window.setInterval(() => {
      setShownLen((n) => {
        const t = targetRef.current;
        if (n >= t) {
          return n;
        }
        const gap = t - n;
        const step = Math.max(1, Math.min(maxStep, Math.ceil(gap / 5)));
        return Math.min(t, n + step);
      });
    }, intervalMs);
    return () => clearInterval(id);
  }, [active, maxStep, intervalMs]);

  useEffect(() => {
    if (!active) {
      setShownLen(fullText.length);
    }
  }, [active, fullText]);

  if (!active) {
    return fullText;
  }
  return fullText.slice(0, Math.min(shownLen, fullText.length));
}
