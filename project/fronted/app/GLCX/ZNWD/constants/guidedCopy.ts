import type { ZnwdLocale } from '../hooks/useZnwdAppearance';
import type { GuidedAspectKind } from './guidedSchema';

export interface GuidedCopyLabels {
  greeting: string;
  aspect: string;
  slotRelation: string;
  slotProperties: string;
  slotDocument: string;
  slotDefault: string;
  preview: string;
  fill: string;
  send: string;
  restart: string;
  back: string;
  quick: string;
  pickTemplate: string;
  nodeTypePlaceholder: string;
  aspectPlaceholder: string;
  title: string;
  collapse: string;
  expand: string;
  pagePrev: string;
  pageNext: string;
  pageCurrent: string;
  modeGuide: string;
  modeFree: string;
  previewEmbeddedHint: string;
  placeholderGuide: string;
  placeholderFree: string;
  sendGuided: string;
  sendFree: string;
}

export function getGuidedCopy(locale: ZnwdLocale): GuidedCopyLabels {
  if (locale === 'en') {
    return {
      greeting:
        'Hi! I can help you explore inventory, relations, and documents. What type of knowledge are you interested in?',
      aspect: 'About "{nodeLabel}", what would you like to know?',
      slotRelation: 'Please select the {slotLabel} you care about:',
      slotProperties: 'Please specify the {slotLabel} to view properties:',
      slotDocument: 'Please enter {slotLabel}:',
      slotDefault: 'Please provide {slotLabel}:',
      preview: 'Here is your question. Please confirm:',
      fill: 'Fill input',
      send: 'Send now',
      restart: 'Start over',
    back: 'Back',
    quick: 'Quick examples',
    pickTemplate: 'Question style',
    pagePrev: 'Previous',
    pageNext: 'Next',
    pageCurrent: 'Current',
      nodeTypePlaceholder: 'Select object type',
      aspectPlaceholder: 'Select aspect',
      title: 'Smart guide',
      collapse: 'Collapse',
      expand: 'Guide me',
      previewEmbeddedHint: 'Question filled below — edit if needed, then press Enter to send',
      modeGuide: 'Guided',
      modeFree: 'Free ask',
      placeholderGuide: 'Complete the steps above; your question appears here',
      placeholderFree: 'Type your question, Enter to send, Shift+Enter for newline',
      sendGuided: 'Send guided query',
      sendFree: 'Send',
    };
  }
  return {
    greeting: '您好！我可以帮您查清单、关联和文档知识。请先选择您关心的对象类型：',
    aspect: '好的，关于「{nodeLabel}」，您想了解哪方面？',
    slotRelation: '请选择您关心的{slotLabel}：',
    slotProperties: '请指定要查看属性的{slotLabel}：',
    slotDocument: '请输入{slotLabel}：',
    slotDefault: '请填写{slotLabel}：',
    preview: '已为您生成问句，请确认：',
    fill: '填入输入框',
    send: '直接发送',
    restart: '重新开始',
    back: '上一步',
    quick: '快捷示例',
    pickTemplate: '问法模板',
    pagePrev: '上一题',
    pageNext: '下一题',
    pageCurrent: '当前',
    nodeTypePlaceholder: '选择对象类型',
    aspectPlaceholder: '选择查询方面',
    title: '智能引导',
    collapse: '收起引导',
    expand: '展开引导',
    previewEmbeddedHint: '问句已填入下方，可编辑后按 Enter 发送',
    modeGuide: '引导问答',
    modeFree: '自由提问',
    placeholderGuide: '完成上方引导后，问句将出现在这里',
    placeholderFree: '请输入您的问题，Enter 发送，Shift+Enter 换行',
    sendGuided: '发送引导问句',
    sendFree: '发送',
  };
}

export function slotPromptForKind(
  kind: GuidedAspectKind | string | undefined,
  slotLabel: string,
  copy: GuidedCopyLabels,
): string {
  const label = slotLabel || '';
  switch (kind) {
    case 'properties':
      return copy.slotProperties.replace('{slotLabel}', label);
    case 'document':
      return copy.slotDocument.replace('{slotLabel}', label);
    case 'relation':
    case 'composite':
      return copy.slotRelation.replace('{slotLabel}', label);
    default:
      return copy.slotDefault.replace('{slotLabel}', label);
  }
}
