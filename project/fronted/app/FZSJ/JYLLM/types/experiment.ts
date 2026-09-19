// types/experiment.ts
export interface ThinkingStep {
  id: string;
  content: string;
  timestamp: string;
  type: 'analysis' | 'reasoning' | 'conclusion';
  context?: string;
}

export interface DraftVersion {
  version: number;
  content: string;
  timestamp: string;
  basedOnAnswers: string[];
}

export interface Question {
  id: string;
  text: string;
  type: 'text' | 'choice' | 'number';
  options?: string[];
  priority: 'high' | 'medium' | 'low';
  reasoning?: string;
}

export interface StreamingState {
  sessionId: string | null;
  isStreaming: boolean;
  currentThinking: ThinkingStep[];
  currentDraft: string;
  draftVersions: DraftVersion[];
  questions: Question[];
  answers: Record<string, string>;
  completion: number;
  status: {
    isComplete: boolean;
    downloadUrl?: string;
    finalDocument?: string;
  };
  showThinkingDrawer: boolean;
  expandedThinking: boolean;
}

export interface ExperimentSession {
  id: string;
  domain: string;
  metadata: Record<string, any>;
  currentState: {
    thinkingLog: ThinkingStep[];
    draftVersions: DraftVersion[];
    questionsAsked: string[];
    answers: Record<string, string>;
    completionPercentage: number;
    questions: Question[];
    firstDraft?: string;
    status?: Record<string, any>;
    completed?: boolean;
    finalDocument?: string;
    downloadUrl?: string;
  };
  createdAt: string;
  lastUpdate: string;
}