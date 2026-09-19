export type GuidedAspectKind = 'enum' | 'relation' | 'properties' | 'document' | 'composite';

export interface GuidedSlot {
  slot_key: string;
  node_type: string;
  label: string;
  required?: boolean;
  free_text?: boolean;
}

export interface GuidedIntent {
  id: string;
  goal_type: string;
  label: string;
  template_id: string;
  slots: GuidedSlot[];
  question_templates: string[];
}

export interface GuidedComposite {
  id: string;
  label: string;
  template_id: string;
  slots: GuidedSlot[];
  question_templates: string[];
}

export interface GuidedFlowAspect {
  id: string;
  labelZh: string;
  labelEn: string;
  aspectKind: GuidedAspectKind | string;
  intentId: string;
}

export interface GuidedFlowNodeType {
  type: string;
  labelZh: string;
  labelEn: string;
  aspects: GuidedFlowAspect[];
}

export interface GuidedFlow {
  greetingZh: string;
  greetingEn: string;
  nodeTypes: GuidedFlowNodeType[];
}

export interface GuidedSchema {
  nodeTypes: { type: string; labelZh: string }[];
  intents: GuidedIntent[];
  composites: GuidedComposite[];
  quickExamples: string[];
  slotKeys: Record<string, string>;
  guidedFlow?: GuidedFlow;
}

export const FALLBACK_GUIDED_FLOW: GuidedFlow = {
  greetingZh: '您好！我可以帮您查清单、关联和文档知识。',
  greetingEn: 'Hi! I can help you explore inventory, relations, and documents.',
  nodeTypes: [
    {
      type: 'EquipmentType',
      labelZh: '装备类型',
      labelEn: 'Equipment type',
      aspects: [
        {
          id: 'enum_list',
          labelZh: '有哪些装备类型',
          labelEn: 'List equipment types',
          aspectKind: 'enum',
          intentId: 'goal_equipment_type',
        },
      ],
    },
    {
      type: 'Equipment',
      labelZh: '装备',
      labelEn: 'Equipment',
      aspects: [
        {
          id: 'enum_list',
          labelZh: '有哪些装备',
          labelEn: 'List equipment',
          aspectKind: 'enum',
          intentId: 'goal_equipment',
        },
        {
          id: 'rel_projects',
          labelZh: '关联的试验项目',
          labelEn: 'Related test projects',
          aspectKind: 'relation',
          intentId: 'goal_equipment_projects',
        },
        {
          id: 'rel_indicators',
          labelZh: '关联的考核指标',
          labelEn: 'Related indicators',
          aspectKind: 'relation',
          intentId: 'goal_indicators',
        },
        {
          id: 'composite_env_projects',
          labelZh: '指定环境下的试验项目',
          labelEn: 'Projects in environment',
          aspectKind: 'composite',
          intentId: 'composite_equip_env_projects',
        },
      ],
    },
    {
      type: 'Environment',
      labelZh: '试验环境',
      labelEn: 'Environment',
      aspects: [
        {
          id: 'enum_list',
          labelZh: '有哪些试验环境',
          labelEn: 'List environments',
          aspectKind: 'enum',
          intentId: 'goal_environment',
        },
        {
          id: 'rel_projects',
          labelZh: '环境下的试验项目',
          labelEn: 'Test projects in environment',
          aspectKind: 'relation',
          intentId: 'goal_env_projects',
        },
        {
          id: 'properties',
          labelZh: '环境分类与属性',
          labelEn: 'Environment properties',
          aspectKind: 'properties',
          intentId: 'goal_env_properties',
        },
      ],
    },
    {
      type: 'TestProject',
      labelZh: '试验项目',
      labelEn: 'Test project',
      aspects: [
        {
          id: 'rel_methods',
          labelZh: '项目的试验方法',
          labelEn: 'Test methods for project',
          aspectKind: 'relation',
          intentId: 'goal_project_methods',
        },
        {
          id: 'rel_equipment',
          labelZh: '项目涉及的装备',
          labelEn: 'Equipment for project',
          aspectKind: 'relation',
          intentId: 'goal_equipment_by_project',
        },
      ],
    },
    {
      type: 'Indicator',
      labelZh: '考核指标',
      labelEn: 'Indicator',
      aspects: [
        {
          id: 'rel_methods',
          labelZh: '指标的试验方法',
          labelEn: 'Test methods for indicator',
          aspectKind: 'relation',
          intentId: 'goal_indicator_methods',
        },
      ],
    },
    {
      type: 'File',
      labelZh: '标准/文档',
      labelEn: 'Standard / document',
      aspects: [
        {
          id: 'document',
          labelZh: '查标准/文档内容',
          labelEn: 'Document lookup',
          aspectKind: 'document',
          intentId: 'goal_document',
        },
        {
          id: 'chapter',
          labelZh: '查标准章节',
          labelEn: 'Standard chapter',
          aspectKind: 'document',
          intentId: 'goal_chapter',
        },
      ],
    },
  ],
};

export const FALLBACK_GUIDED_SCHEMA: GuidedSchema = {
  nodeTypes: [
    { type: 'EquipmentType', labelZh: '装备类型' },
    { type: 'Equipment', labelZh: '装备' },
    { type: 'Environment', labelZh: '试验环境' },
    { type: 'TestProject', labelZh: '试验项目' },
    { type: 'Indicator', labelZh: '考核指标' },
    { type: 'TestMethod', labelZh: '试验方法' },
    { type: 'File', labelZh: '标准/文档' },
  ],
  intents: [
    {
      id: 'goal_equipment_type',
      goal_type: 'EquipmentType',
      label: '查装备类型',
      template_id: 'enum_equipment_type',
      slots: [],
      question_templates: ['有哪些装备类型'],
    },
    {
      id: 'goal_equipment',
      goal_type: 'Equipment',
      label: '查装备清单',
      template_id: 'enum_equipment',
      slots: [],
      question_templates: ['有哪些装备'],
    },
    {
      id: 'goal_equipment_projects',
      goal_type: 'TestProject',
      label: '查装备的试验项目',
      template_id: 'equip_to_projects',
      slots: [{ slot_key: 'equipment_keywords', node_type: 'Equipment', label: '装备', required: true }],
      question_templates: ['{equipment}有哪些试验项目'],
    },
    {
      id: 'goal_environment',
      goal_type: 'Environment',
      label: '查试验环境',
      template_id: 'enum_environment',
      slots: [],
      question_templates: ['有哪些试验环境'],
    },
    {
      id: 'goal_env_projects',
      goal_type: 'TestProject',
      label: '查环境下的试验项目',
      template_id: 'env_to_projects',
      slots: [{ slot_key: 'environment_keywords', node_type: 'Environment', label: '试验环境', required: true }],
      question_templates: ['{environment}环境下有哪些试验项目'],
    },
    {
      id: 'goal_env_properties',
      goal_type: 'Environment',
      label: '查环境属性与分类',
      template_id: 'environment_properties',
      slots: [{ slot_key: 'environment_keywords', node_type: 'Environment', label: '试验环境', required: true }],
      question_templates: ['{environment}的分类和属性有哪些'],
    },
    {
      id: 'goal_indicators',
      goal_type: 'Indicator',
      label: '查装备考核指标',
      template_id: 'equip_to_indicators',
      slots: [{ slot_key: 'equipment_keywords', node_type: 'Equipment', label: '装备', required: true }],
      question_templates: ['{equipment}有哪些考核指标'],
    },
    {
      id: 'goal_project_methods',
      goal_type: 'TestMethod',
      label: '查项目的试验方法',
      template_id: 'project_to_methods',
      slots: [{ slot_key: 'project_keywords', node_type: 'TestProject', label: '试验项目', required: true }],
      question_templates: [
        '{project}的试验方法是什么',
        '{project}有哪些试验方法',
        '查询{project}对应的试验方法',
        '哪些试验方法适用于{project}',
      ],
    },
    {
      id: 'goal_indicator_methods',
      goal_type: 'TestMethod',
      label: '查指标的试验方法',
      template_id: 'indicator_to_methods',
      slots: [{ slot_key: 'indicator_keywords', node_type: 'Indicator', label: '考核指标', required: true }],
      question_templates: ['{indicator}的试验方法是什么'],
    },
    {
      id: 'goal_document',
      goal_type: 'File',
      label: '查标准/文档',
      template_id: 'document_lookup',
      slots: [{ slot_key: 'standard_keywords', node_type: 'File', label: '标准关键词', required: true, free_text: true }],
      question_templates: ['{standard}的主要内容是什么'],
    },
    {
      id: 'goal_chapter',
      goal_type: 'File',
      label: '查标准章节',
      template_id: 'standard_chapter',
      slots: [
        { slot_key: 'standard_keywords', node_type: 'File', label: '标准号/名称', required: true, free_text: true },
        { slot_key: 'section', node_type: 'File', label: '章节号', required: true, free_text: true },
      ],
      question_templates: ['{standard}第{section}节讲了什么'],
    },
  ],
  composites: [
    {
      id: 'composite_equip_env_projects',
      label: '装备 + 环境 → 项目',
      template_id: 'equip_and_env_to_projects',
      slots: [
        { slot_key: 'equipment_keywords', node_type: 'Equipment', label: '装备', required: true },
        { slot_key: 'environment_keywords', node_type: 'Environment', label: '试验环境', required: true },
      ],
      question_templates: ['{equipment}在{environment}环境下有哪些试验项目'],
    },
  ],
  quickExamples: [
    '有哪些设备类型？',
    '无人机有什么试验项目？',
    '坦克有什么考核指标？',
    '复杂电磁环境分类有哪些？',
  ],
  slotKeys: {},
  guidedFlow: FALLBACK_GUIDED_FLOW,
};

const SLOT_PLACEHOLDERS: Record<string, string> = {
  equipment_keywords: 'equipment',
  equipment_type_keywords: 'equipment_type',
  environment_keywords: 'environment',
  project_keywords: 'project',
  indicator_keywords: 'indicator',
  method_keywords: 'method',
  standard_keywords: 'standard',
  section: 'section',
};

export function slotValueKey(slotKey: string): string {
  const bare = SLOT_PLACEHOLDERS[slotKey] || slotKey.replace(/_keywords$/, '');
  return bare;
}

export function composeGuidedQuestion(
  template: string,
  values: Record<string, string>
): string {
  let out = template;
  for (const [k, v] of Object.entries(values)) {
    out = out.split(`{${k}}`).join(v);
  }
  for (const [sk, ph] of Object.entries(SLOT_PLACEHOLDERS)) {
    const v = values[sk] || values[ph] || '';
    if (v) out = out.split(`{${ph}}`).join(v);
  }
  return out.replace(/\{[^}]+\}/g, '').replace(/\s+/g, ' ').trim();
}
