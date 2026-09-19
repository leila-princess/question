import { getGuidedCopy, slotPromptForKind } from './guidedCopy';
import {
  composeGuidedQuestion,
  slotValueKey,
  type GuidedComposite,
  type GuidedFlow,
  type GuidedFlowAspect,
  type GuidedFlowNodeType,
  type GuidedIntent,
  type GuidedSchema,
  type GuidedSlot,
} from './guidedSchema';
import type { ZnwdLocale } from '../hooks/useZnwdAppearance';

export type GuidedPhase = 'greeting' | 'node_type' | 'aspect' | 'slot' | 'preview' | 'done';
export type GuidedControlType = 'node_type' | 'aspect' | 'slot' | 'preview';

export interface GuidedHistoryEntry {
  promptText: string;
  answerSummary?: string;
}

export interface GuidedFlowState {
  nodeType?: string;
  aspectId?: string;
  intentId?: string;
  intentMode?: 'intent' | 'composite';
  aspectKind?: string;
  slotValues: Record<string, string>;
  slotBindings: Record<string, GuidedSlotBinding>;
  slotIndex: number;
  templateIdx: number;
  history: GuidedHistoryEntry[];
}

export interface GuidedSlotBinding {
  slot_key: string;
  node_type: string;
  name: string;
  entity_id?: string;
  role?: 'source' | 'target' | 'anchor' | 'goal';
}

export interface GuidedExecutePayload {
  question: string;
  templateId: string;
  intentId: string;
  goalType?: string;
  bindings: GuidedSlotBinding[];
  aspectKind?: string;
}

export interface GuidedTurn {
  phase: GuidedPhase;
  promptText: string;
  controlType?: GuidedControlType;
  slot?: GuidedSlot;
  aspectKind?: string;
  nodeTypeOptions?: GuidedFlowNodeType[];
  aspectOptions?: GuidedFlowAspect[];
  resolvedItem?: GuidedIntent | GuidedComposite;
  preview?: string;
  templates?: string[];
  canGoBack: boolean;
}

export function createInitialFlowState(): GuidedFlowState {
  return {
    slotValues: {},
    slotBindings: {},
    slotIndex: 0,
    templateIdx: 0,
    history: [],
  };
}

function resolveGuidedFlow(schema: GuidedSchema): GuidedFlow {
  if (schema.guidedFlow?.nodeTypes?.length) {
    return schema.guidedFlow;
  }
  return buildGuidedFlowFromIntents(schema);
}

function buildGuidedFlowFromIntents(schema: GuidedSchema): GuidedFlow {
  const nodeLabelMap = Object.fromEntries(
    (schema.nodeTypes || []).map((n) => [n.type, n.labelZh]),
  );
  const aspectsByGoal: Record<string, GuidedFlowAspect[]> = {};

  for (const intent of schema.intents) {
    const nt = intent.goal_type;
    if (!aspectsByGoal[nt]) aspectsByGoal[nt] = [];
    const kind = (intent.slots || []).length === 0 ? 'enum' : 'relation';
    aspectsByGoal[nt].push({
      id: intent.id,
      labelZh: intent.label,
      labelEn: intent.label,
      aspectKind: kind,
      intentId: intent.id,
    });
  }

  for (const composite of schema.composites) {
    const nt = composite.slots?.[0]?.node_type || 'Equipment';
    if (!aspectsByGoal[nt]) aspectsByGoal[nt] = [];
    aspectsByGoal[nt].push({
      id: composite.id,
      labelZh: composite.label,
      labelEn: composite.label,
      aspectKind: 'composite',
      intentId: composite.id,
    });
  }

  return {
    greetingZh: '您好！我可以帮您查清单、关联和文档知识。',
    greetingEn: 'Hi! I can help you explore inventory, relations, and documents.',
    nodeTypes: Object.entries(aspectsByGoal).map(([type, aspects]) => ({
      type,
      labelZh: nodeLabelMap[type] || type,
      labelEn: type,
      aspects,
    })),
  };
}

function resolveIntentItem(
  schema: GuidedSchema,
  intentId: string,
): { item: GuidedIntent | GuidedComposite; mode: 'intent' | 'composite' } | null {
  const intent = schema.intents.find((i) => i.id === intentId);
  if (intent) return { item: intent, mode: 'intent' };
  const composite = schema.composites.find((c) => c.id === intentId);
  if (composite) return { item: composite, mode: 'composite' };
  return null;
}

function nodeTypeEntry(flow: GuidedFlow, nodeType?: string): GuidedFlowNodeType | undefined {
  return flow.nodeTypes.find((n) => n.type === nodeType);
}

function aspectEntry(
  entry: GuidedFlowNodeType | undefined,
  aspectId?: string,
): GuidedFlowAspect | undefined {
  return entry?.aspects.find((a) => a.id === aspectId);
}

function buildPreview(
  item: GuidedIntent | GuidedComposite,
  slotValues: Record<string, string>,
  templateIdx: number,
): { preview: string; templates: string[] } {
  const templates = item.question_templates || [];
  const tpl = templates[templateIdx] || templates[0] || '';
  const slots = item.slots || [];
  const vals: Record<string, string> = {};
  for (const s of slots) {
    const v = slotValues[s.slot_key] || '';
    vals[slotValueKey(s.slot_key)] = v;
    vals[s.slot_key] = v;
  }
  return { preview: composeGuidedQuestion(tpl, vals), templates };
}

function allSlotsFilled(item: GuidedIntent | GuidedComposite, slotValues: Record<string, string>): boolean {
  return (item.slots || []).every(
    (s) => !s.required || (slotValues[s.slot_key] || '').trim().length > 0,
  );
}

export function getCurrentTurn(
  schema: GuidedSchema,
  state: GuidedFlowState,
  locale: ZnwdLocale,
): GuidedTurn {
  const copy = getGuidedCopy(locale);
  const flow = resolveGuidedFlow(schema);
  const greeting =
    locale === 'en'
      ? flow.greetingEn || copy.greeting
      : flow.greetingZh || copy.greeting;

  if (!state.nodeType) {
    return {
      phase: 'greeting',
      promptText: greeting,
      controlType: 'node_type',
      nodeTypeOptions: flow.nodeTypes,
      canGoBack: false,
    };
  }

  const ntEntry = nodeTypeEntry(flow, state.nodeType);
  const nodeLabel =
    locale === 'en'
      ? ntEntry?.labelEn || state.nodeType
      : ntEntry?.labelZh || state.nodeType;

  if (!state.aspectId || !state.intentId) {
    return {
      phase: 'aspect',
      promptText: copy.aspect.replace('{nodeLabel}', nodeLabel),
      controlType: 'aspect',
      aspectOptions: ntEntry?.aspects || [],
      canGoBack: true,
    };
  }

  const resolved = resolveIntentItem(schema, state.intentId);
  if (!resolved) {
    return {
      phase: 'aspect',
      promptText: copy.aspect.replace('{nodeLabel}', nodeLabel),
      controlType: 'aspect',
      aspectOptions: ntEntry?.aspects || [],
      canGoBack: true,
    };
  }

  const { item } = resolved;
  const slots = item.slots || [];

  if (state.slotIndex < slots.length) {
    const slot = slots[state.slotIndex];
    const filled = (state.slotValues[slot.slot_key] || '').trim();
    if (!filled) {
      return {
        phase: 'slot',
        promptText: slotPromptForKind(state.aspectKind, slot.label, copy),
        controlType: 'slot',
        slot,
        aspectKind: state.aspectKind,
        resolvedItem: item,
        canGoBack: true,
      };
    }
  }

  if (!allSlotsFilled(item, state.slotValues)) {
    const nextIdx = slots.findIndex(
      (s) => !(state.slotValues[s.slot_key] || '').trim(),
    );
    const slot = slots[nextIdx >= 0 ? nextIdx : 0];
    return {
      phase: 'slot',
      promptText: slotPromptForKind(state.aspectKind, slot?.label || '', copy),
      controlType: 'slot',
      slot,
      aspectKind: state.aspectKind,
      resolvedItem: item,
      canGoBack: true,
    };
  }

  const { preview, templates } = buildPreview(item, state.slotValues, state.templateIdx);
  return {
    phase: 'preview',
    promptText: copy.preview,
    controlType: 'preview',
    preview,
    templates,
    resolvedItem: item,
    canGoBack: true,
  };
}

export function selectNodeType(
  schema: GuidedSchema,
  state: GuidedFlowState,
  nodeType: string,
  locale: ZnwdLocale,
): GuidedFlowState {
  const flow = resolveGuidedFlow(schema);
  const nt = nodeTypeEntry(flow, nodeType);
  const label =
    locale === 'en' ? nt?.labelEn || nodeType : nt?.labelZh || nodeType;
  const turn = getCurrentTurn(schema, state, locale);
  return {
    ...createInitialFlowState(),
    nodeType,
    history: [
      ...state.history,
      { promptText: turn.promptText, answerSummary: label },
    ],
  };
}

export function selectAspect(
  schema: GuidedSchema,
  state: GuidedFlowState,
  aspectId: string,
  locale: ZnwdLocale,
): GuidedFlowState {
  const flow = resolveGuidedFlow(schema);
  const nt = nodeTypeEntry(flow, state.nodeType);
  const aspect = aspectEntry(nt, aspectId);
  if (!aspect) return state;

  const turn = getCurrentTurn(schema, state, locale);
  const label = locale === 'en' ? aspect.labelEn : aspect.labelZh;
  const resolved = resolveIntentItem(schema, aspect.intentId);

  return {
    ...state,
    aspectId,
    intentId: aspect.intentId,
    intentMode: resolved?.mode,
    aspectKind: aspect.aspectKind,
    slotValues: {},
    slotBindings: {},
    slotIndex: 0,
    templateIdx: 0,
    history: [
      ...state.history,
      { promptText: turn.promptText, answerSummary: label },
    ],
  };
}

export function fillSlot(
  schema: GuidedSchema,
  state: GuidedFlowState,
  slotKey: string,
  value: string,
  locale: ZnwdLocale,
  meta?: { node_type?: string; entity_id?: string },
): GuidedFlowState {
  const resolved = state.intentId ? resolveIntentItem(schema, state.intentId) : null;
  if (!resolved) return state;

  const slots = resolved.item.slots || [];
  const slotIdx = slots.findIndex((s) => s.slot_key === slotKey);
  const turn = getCurrentTurn(schema, state, locale);

  const nextValues = { ...state.slotValues, [slotKey]: value };
  const nextBindings = { ...state.slotBindings };
  const slotDef = slots.find((s) => s.slot_key === slotKey);
  if (slotDef) {
    nextBindings[slotKey] = {
      slot_key: slotKey,
      node_type: meta?.node_type || slotDef.node_type,
      name: value,
      role: 'source',
      ...(meta?.entity_id ? { entity_id: meta.entity_id } : {}),
    };
  }
  const nextSlotIndex = slotIdx + 1;

  const historyEntry = {
    promptText: turn.promptText,
    answerSummary: value,
  };

  if (nextSlotIndex >= slots.length) {
    return {
      ...state,
      slotValues: nextValues,
      slotBindings: nextBindings,
      slotIndex: slots.length,
      history: [...state.history, historyEntry],
    };
  }

  return {
    ...state,
    slotValues: nextValues,
    slotBindings: nextBindings,
    slotIndex: nextSlotIndex,
    history: [...state.history, historyEntry],
  };
}

export function buildGuidedExecutePayload(
  schema: GuidedSchema,
  state: GuidedFlowState,
  question: string,
): GuidedExecutePayload | null {
  const q = (question || '').trim();
  const intentId = state.intentId;
  if (!q || !intentId) return null;
  const resolved = resolveIntentItem(schema, intentId);
  if (!resolved) return null;
  const templateId = resolved.item.template_id;
  if (!templateId) return null;
  const goalType = 'goal_type' in resolved.item ? resolved.item.goal_type : undefined;
  const sourceBindings = Object.values(state.slotBindings).map((b) => ({
    ...b,
    role: (b.role || 'source') as GuidedSlotBinding['role'],
  }));
  const bindings: GuidedSlotBinding[] = [...sourceBindings];
  if (goalType) {
    bindings.push({
      slot_key: 'goal_node_type',
      node_type: goalType,
      name: goalType,
      role: 'target',
    });
  }
  return {
    question: q,
    templateId,
    intentId,
    goalType,
    bindings,
    aspectKind: state.aspectKind,
  };
}

export function setTemplateIdx(state: GuidedFlowState, idx: number): GuidedFlowState {
  return { ...state, templateIdx: idx };
}

export function goBackFlowState(
  schema: GuidedSchema,
  state: GuidedFlowState,
  locale: ZnwdLocale = 'zh',
): GuidedFlowState {
  const history = [...state.history];
  if (history.length === 0) return createInitialFlowState();

  history.pop();

  const currentPhase = getCurrentTurn(schema, state, locale).phase;

  if (currentPhase === 'preview') {
    const resolved = state.intentId ? resolveIntentItem(schema, state.intentId) : null;
    const slots = resolved?.item.slots || [];
    if (slots.length === 0) {
      return {
        ...state,
        aspectId: undefined,
        intentId: undefined,
        intentMode: undefined,
        aspectKind: undefined,
        slotValues: {},
        slotBindings: {},
        slotIndex: 0,
        templateIdx: 0,
        history,
      };
    }
    const lastSlot = slots[slots.length - 1];
    const nextValues = { ...state.slotValues };
    const nextBindings = { ...state.slotBindings };
    delete nextValues[lastSlot.slot_key];
    delete nextBindings[lastSlot.slot_key];
    return {
      ...state,
      slotValues: nextValues,
      slotBindings: nextBindings,
      slotIndex: slots.length - 1,
      history,
    };
  }

  const resolved = state.intentId ? resolveIntentItem(schema, state.intentId) : null;
  const slots = resolved?.item.slots || [];

  if (state.slotIndex > 0 && slots.length > 0) {
    const prevSlot = slots[state.slotIndex - 1];
    const nextValues = { ...state.slotValues };
    const nextBindings = { ...state.slotBindings };
    if (prevSlot) {
      delete nextValues[prevSlot.slot_key];
      delete nextBindings[prevSlot.slot_key];
    }
    return {
      ...state,
      slotValues: nextValues,
      slotBindings: nextBindings,
      slotIndex: Math.max(0, state.slotIndex - 1),
      history,
    };
  }

  if (state.aspectId) {
    return {
      nodeType: state.nodeType,
      slotValues: {},
      slotBindings: {},
      slotIndex: 0,
      templateIdx: 0,
      history,
    };
  }

  if (state.nodeType) {
    return {
      ...createInitialFlowState(),
      history,
    };
  }

  return createInitialFlowState();
}
