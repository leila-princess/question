import type { UploadFileMeta } from '@/app/ZSGL/WJGL/utils/wjglUploadPipeline';

export type SchemeExportFormat = 'md' | 'pdf' | 'word';

function todayDate(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function getIssuer(): string {
  if (typeof window === 'undefined') return '';
  return (localStorage.getItem('userName') || '').trim().slice(0, 80);
}

function formatList(names: string[], max = 5): string {
  const list = names.filter(Boolean);
  if (list.length === 0) return '';
  const shown = list.slice(0, max).join('、');
  return list.length > max ? `${shown}......` : shown;
}

function buildAbstract(equipment: string, environment: string, projects: string[], indicators: string[]): string {
  const eq = equipment || '未知装备';
  const env = environment || '未知环境';
  const projStr = formatList(projects) || '相关';
  const indStr = formatList(indicators) || '相关';
  return `${eq}装备在${env}环境下边界性能考核指标的试验方案，包括：${projStr}项目......，${indStr}指标.....`;
}

function dedupeKeywords(...groups: string[][]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const g of groups) {
    for (const raw of g) {
      const k = String(raw || '').trim();
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push(k);
    }
  }
  return out.slice(0, 20);
}

export interface JyllmKmMetaInput {
  schemeName?: string;
  taskId: string;
  deviceDisplayName?: string;
  environmentConditionsText?: string;
  projects?: Array<string | { name?: string; id?: string }>;
  indicators?: Array<string | { name?: string; id?: string }>;
  fileType?: string;
}

export interface JygzKmMetaInput {
  equipmentName?: string;
  docNumber?: string;
  taskId?: string;
  environments?: Array<{ env_name?: string } | string>;
  projects?: string[];
  indicators?: string[];
  schemeName?: string;
  fileType?: string;
}

function resolveNames(items?: Array<string | { name?: string; id?: string }>): string[] {
  if (!items?.length) return [];
  return items.map((x) => {
    if (typeof x === 'string') return x;
    return String(x.name || x.id || '').trim();
  }).filter(Boolean);
}

export function buildJyllmKmMeta(input: JyllmKmMetaInput): UploadFileMeta {
  const equipment = (input.deviceDisplayName || '').trim();
  const environment = (input.environmentConditionsText || '').trim();
  const projects = resolveNames(input.projects);
  const indicators = resolveNames(input.indicators);
  const title = (input.schemeName || '').trim() || (equipment ? `${equipment}试验方案` : `方案 ${input.taskId.slice(0, 8)}`);
  const ext = input.fileType || 'docx';

  return {
    title,
    std_name: title,
    std_code: input.taskId,
    publish_date: todayDate(),
    issuer: getIssuer(),
    abstract: buildAbstract(equipment, environment, projects, indicators),
    keywords: dedupeKeywords(
      equipment ? [equipment] : [],
      environment ? [environment] : [],
      projects,
      indicators,
      ['试验方案'],
    ),
    file_type: ext,
  };
}

export function buildJygzKmMeta(input: JygzKmMetaInput): UploadFileMeta {
  const equipment = (input.equipmentName || '').trim();
  const envNames = (input.environments || []).map((e) => {
    if (typeof e === 'string') return e;
    return String(e.env_name || '').trim();
  }).filter(Boolean);
  const environment = envNames.join('、') || '';
  const projects = input.projects || [];
  const indicators = input.indicators || [];
  const title = (input.schemeName || '').trim() || (equipment ? `${equipment}试验方案` : '试验方案');
  const ext = input.fileType || 'docx';

  return {
    title,
    std_name: title,
    std_code: (input.docNumber || input.taskId || '').trim(),
    publish_date: todayDate(),
    issuer: getIssuer(),
    abstract: buildAbstract(equipment, environment, projects, indicators),
    keywords: dedupeKeywords(
      equipment ? [equipment] : [],
      envNames,
      projects,
      indicators,
      ['试验方案'],
    ),
    file_type: ext,
  };
}

export function schemeFilename(base: string, format: SchemeExportFormat): string {
  const ext = format === 'word' ? 'docx' : format === 'pdf' ? 'pdf' : 'md';
  return `scheme_${base}.${ext}`;
}

export function blobToFile(blob: Blob, filename: string): File {
  return new File([blob], filename, { type: blob.type || 'application/octet-stream' });
}
