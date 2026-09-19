/** 环境五要素：与 Neo4j Environment 节点字段及 JYGZ environment-settings 一致 */

export interface EnvironmentFiveElementsItem {
  env_name: string;
  type: string;
  class: string;
  limit: string;
  request: string;
  combination: string;
}

export type EnvironmentFiveElementsField =
  | "type"
  | "class"
  | "combination"
  | "limit"
  | "request";

export const ENVIRONMENT_FIVE_ELEMENT_TABS: {
  key: EnvironmentFiveElementsField;
  label: string;
}[] = [
  { key: "type", label: "分类" },
  { key: "class", label: "分级" },
  { key: "combination", label: "组合" },
  { key: "limit", label: "约束" },
  { key: "request", label: "要求" },
];

const PROXY_BASE = "/api/GLCX/JQCX";

let settingsByEnvName: Map<string, EnvironmentFiveElementsItem> | null = null;
let loadPromise: Promise<Map<string, EnvironmentFiveElementsItem>> | null = null;

function buildMap(list: EnvironmentFiveElementsItem[]): Map<string, EnvironmentFiveElementsItem> {
  const map = new Map<string, EnvironmentFiveElementsItem>();
  for (const item of list) {
    const name = String(item.env_name || "").trim();
    if (name) map.set(name, item);
  }
  return map;
}

/** 首次调用时拉取全部环境五要素，之后使用内存缓存 */
export async function ensureEnvironmentFiveElementsLoaded(): Promise<
  Map<string, EnvironmentFiveElementsItem>
> {
  if (settingsByEnvName) return settingsByEnvName;
  if (!loadPromise) {
    loadPromise = (async () => {
      const url = new URL(PROXY_BASE, window.location.origin);
      url.searchParams.set("path", "/environment-settings-all");
      const res = await fetch(url.toString());
      const json = (await res.json()) as {
        success?: boolean;
        data?: EnvironmentFiveElementsItem[];
        error?: string;
      };
      if (!json.success) {
        throw new Error(json.error || "获取环境五要素失败");
      }
      const list = Array.isArray(json.data) ? json.data : [];
      settingsByEnvName = buildMap(list);
      return settingsByEnvName;
    })().catch((err) => {
      loadPromise = null;
      throw err;
    });
  }
  return loadPromise;
}

export function getEnvironmentFiveElements(envName: string): EnvironmentFiveElementsItem | undefined {
  return settingsByEnvName?.get(envName);
}

export function clearEnvironmentFiveElementsCache(): void {
  settingsByEnvName = null;
  loadPromise = null;
}
