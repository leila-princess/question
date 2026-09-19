export type FlowStep =
  | 'device'
  | 'environment'
  | 'project'
  | 'indicator'
  | 'method'
  | 'planner_thinking'
  | 'planner_review'
  | 'generating'
  | 'reviewing'
  | 'complete';

export type MessageRole = 'user' | 'assistant' | 'system';

export type MessageContentType = 'text' | 'device_select' | 'env_select' | 'project_select' | 'indicator_select' | 'method_select' | 'planner_prompts' | 'thinking' | 'chapter';

export interface PlannerPrompt {
  chapter_index: number;
  chapter_title: string;
  writer_prompt: string;
  reviewer_prompt: string;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  contentType: MessageContentType;
  timestamp: string;
  thinking?: ThinkingEntry[];
  metadata?: {
    devices?: string[];
    environments?: string[];
    projects?: string[];
    indicators?: string[];
    methods?: string[];
    chapterIndex?: number;
    chapterTitle?: string;
    uploadedFiles?: any[];
    plannerPrompts?: PlannerPrompt[];
    /** V2：确认生成时回显的规划分析 + 方案骨架全文 */
    confirmedPlanMarkdown?: string;
    agent?: string;
  };
  isStreaming?: boolean;
  selections?: string[];
}

export interface DeviceSelection {
  searchKey: string;
  selectedDevices: string[]; // ZB_0001, ZB_0002, ...
  modelInput: string;
  /** 用户填写的具体设备名（默认三级设备名，可改） */
  deviceDisplayName?: string;
  /** 用户填写的具体环境条件文本 */
  environmentConditionsText?: string;
}

export interface ReferenceFileOption {
  id: string;
  name: string;
  snippet?: string;
  relevance?: number;
}

export interface OutlineTreeNode {
  id: string;
  title: string;
  type: 'chapter' | 'environment' | 'project' | 'indicator' | 'method';
  level: number;
  hasDbData?: boolean;
  dbPreview?: string;
  dbFull?: string;
  suggestedReferenceFiles?: ReferenceFileOption[];
  children?: OutlineTreeNode[];
}

export interface SectionGenerationOption {
  mode: 'database_correct' | 'llm_generate';
  referenceFileIds: string[];
}

export interface OutlineSelection {
  selectedSections: string[];
  sectionOptions: Record<string, SectionGenerationOption>;
}

export interface EnvironmentSelection {
  selectedEnvs: string[]; // 复杂电磁环境, 复杂地理环境, ...
  envConfig: string;
  librarianSuggestion: string;
}

export interface MatrixCell {
  deviceId: string;
  envId: string;
  items: any[];
  selectedItems: string[];
}

export interface FlowState {
  currentStep: FlowStep;
  completedSteps: FlowStep[];
  canGoBack: boolean;
}

export interface ChapterModule {
  index: number;
  title: string;
  content: string;
  score: number;
  userScore?: number;
  userComment?: string;
  status: 'pending' | 'generating' | 'complete' | 'rewriting' | 'approved';
  thinking?: string;
  keywords?: string;
  searchContext?: string;
}

export interface ThinkingEntry {
  /** planner | planner_outline | context_compress | writer | reviewer */
  agent: string;
  content: string;
  timestamp: string;
  isCollapsed: boolean;
}

export interface HistoryRecord {
  task_id: string;
  scheme_name?: string;
  generation_status?: string;
  created_at: string;
  completed_at?: string;
  word_count: number;
  metadata: any;
}

export type RegionRewriteType = 'selection' | 'heading' | 'method' | 'chapter';

export interface RegionRewriteSpec {
  region_type: RegionRewriteType;
  chapter_index: number;
  chapter_content: string;
  chapter_title?: string;
  selected_text?: string;
  start_offset?: number;
  end_offset?: number;
  heading_id?: string;
  heading_text?: string;
  method_id?: string;
  label?: string;
  original_region?: string;
}

export interface RegionRevisionRecord {
  timestamp: number;
  region_type: RegionRewriteType;
  comment: string;
  chapter_index: number;
  before: string;
  after: string;
}
