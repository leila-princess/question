import { useCallback, useRef, useState } from 'react';

export interface SSEEvent<T = unknown> {
  type: string;
  data: T;
}

export interface RouteClarifyAnswer {
  selectedRouteId: string;
  clarifyEventId: string;
  originalQuestion: string;
  selectedLabel?: string;
}

export interface AnswerCorrectionRequest {
  turnId: string;
  routingEventId?: string;
  originalQuestion: string;
  rating?: 'positive' | 'negative';
  feedbackKind: string;
  correctedRouteId?: string;
  correctedSlots?: Record<string, string[]>;
  predictedSlots?: Record<string, string[] | string>;
  comment?: string;
  rerun: boolean;
}

export interface EntityClarifyAnswer {
  selectedNodeId: string;
  clarifyEventId: string;
  originalQuestion: string;
  parentType?: string;
  childName?: string;
  name?: string;
  selectedLabel?: string;
}

export interface GuidedExecuteBinding {
  slot_key: string;
  node_type: string;
  name: string;
  entity_id?: string;
}

export interface SendMessageOptions {
  question: string;
  sessionId?: string;
  userId?: string;
  llmModelKey?: string;
  maxTokens?: number;
  messages?: any[];
  routeClarify?: RouteClarifyAnswer;
  entityClarify?: EntityClarifyAnswer;
  answerCorrection?: AnswerCorrectionRequest;
  /** 引导式精准执行：跳过 v6 通用路由 */
  templateId?: string;
  intentId?: string;
  goalType?: string;
  bindings?: GuidedExecuteBinding[];
}

export interface UseSSEReturn {
  sendMessage: (
    messageOrOptions: string | SendMessageOptions,
    onEvent: (event: SSEEvent) => void
  ) => Promise<void>;
  stopGeneration: () => void;
  isGenerating: boolean;
}

export const useSSE = (endpoint: string = '/api/znwd/chat/stream'): UseSSEReturn => {
  const controllerRef = useRef<AbortController | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);

  const sendMessage = useCallback(
    async (
      messageOrOptions: string | SendMessageOptions,
      onEvent: (event: SSEEvent) => void
    ): Promise<void> => {
      if (controllerRef.current) {
        controllerRef.current.abort();
      }

      controllerRef.current = new AbortController();
      setIsGenerating(true);

      let requestBody: any;
      if (typeof messageOrOptions === 'string') {
        requestBody = { question: messageOrOptions };
      } else {
        requestBody = {
          question: messageOrOptions.question,
          sessionId: messageOrOptions.sessionId,
          userId: messageOrOptions.userId,
          llmModelKey: messageOrOptions.llmModelKey,
          maxTokens: messageOrOptions.maxTokens,
          messages: messageOrOptions.messages,
          routeClarify: messageOrOptions.routeClarify,
          entityClarify: messageOrOptions.entityClarify,
          answerCorrection: messageOrOptions.answerCorrection,
          templateId: messageOrOptions.templateId,
          bindings: messageOrOptions.bindings,
        };
      }

      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
          signal: controllerRef.current.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('Response body is not readable');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        const parseEventBlock = (block: string) => {
          if (!block.trim()) return;
          const lines = block.split(/\r?\n/);
          let eventType = '';
          const dataLines: string[] = [];

          for (const rawLine of lines) {
            const line = rawLine.trimEnd();
            if (line.startsWith('event:')) {
              eventType = line.substring(6).trim();
            } else if (line.startsWith('data:')) {
              dataLines.push(line.substring(5).trim());
            }
          }

          if (eventType) {
            const dataStr = dataLines.length > 0 ? dataLines.join('\n') : '{}';
            try {
              const data = dataStr.trim() ? JSON.parse(dataStr) : {};
              onEvent({ type: eventType, data });
            } catch (e) {
              console.error('Failed to parse SSE data:', e, 'Raw:', dataStr);
            }
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const eventBlocks = buffer.split(/\r?\n\r?\n/);
          buffer = eventBlocks.pop() || '';

          for (const block of eventBlocks) {
            parseEventBlock(block);
          }
        }
        
        if (buffer.trim()) {
          parseEventBlock(buffer);
        }
      } catch (error: unknown) {
        if (error instanceof Error && error.name !== 'AbortError') {
          console.error('SSE error:', error);
          onEvent({ type: 'error', data: { message: error.message } });
        }
      } finally {
        controllerRef.current = null;
        setIsGenerating(false);
      }
    },
    [endpoint]
  );

  const stopGeneration = useCallback(() => {
    if (controllerRef.current) {
      controllerRef.current.abort();
      controllerRef.current = null;
      setIsGenerating(false);
    }
  }, []);

  return { sendMessage, stopGeneration, isGenerating };
};
