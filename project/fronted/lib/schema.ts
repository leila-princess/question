/**
 * 前端 Schema 工具 — 从 schema.json 自动解析节点/属性/关系
 * 
 * 使用方式：
 *   import { getNodeProperties, getAllNodeTypes } from '@/lib/schema';
 * 
 * 开发模式：直接读取本地 JSON（热更新）
 * 生产模式：通过 API 获取（fetch /api/schema）
 */

import schemaData from '../../backend/schema.json';

// ==================== TypeScript 类型定义 ====================

export interface SchemaProperty {
  [fieldName: string]: string; // 字段名: 中文名
}

export interface SchemaNodeType {
  label: string;
  description: string;
  properties: SchemaProperty;
}

export interface SchemaRelationshipType {
  label: string;
  description: string;
  from: string;
  to: string;
  properties: SchemaProperty;
}

export interface SchemaDefinition {
  version: string;
  lastUpdated: string;
  nodeTypes: Record<string, SchemaNodeType>;
  relationshipTypes: Record<string, SchemaRelationshipType>;
  queryTemplates: Record<string, string>;
}

// ==================== Schema 数据源 ====================

/**
 * 获取 Schema 数据
 * 开发时直接使用本地 JSON，生产时可切换为 API 调用
 */
export function getSchema(): SchemaDefinition {
  return schemaData as unknown as SchemaDefinition;
}

// ==================== 节点操作 ====================

/**
 * 获取所有节点类型
 * 返回: { Equipment: { label: "装备", ... }, ... }
 */
export function getAllNodeTypes(): Record<string, SchemaNodeType> {
  return getSchema().nodeTypes;
}

/**
 * 获取单个节点类型定义
 * @param nodeType 节点类型名，如 "Equipment"
 */
export function getNodeType(nodeType: string): SchemaNodeType | undefined {
  return getSchema().nodeTypes[nodeType];
}

/**
 * 获取节点的所有属性（字段名: 中文名）
 * @param nodeType 节点类型名
 * 返回: { name: "名称", model: "型号", ... }
 */
export function getNodeProperties(nodeType: string): SchemaProperty {
  return getSchema().nodeTypes[nodeType]?.properties || {};
}

/**
 * 获取节点的所有字段名列表
 * @param nodeType 节点类型名
 * 返回: ["name", "model", "category", ...]
 */
export function getNodeFieldNames(nodeType: string): string[] {
  return Object.keys(getNodeProperties(nodeType));
}

/**
 * 获取节点的中文标签映射（用于表格列头、表单标签等）
 * @param nodeType 节点类型名
 * 返回: { name: "名称", model: "型号", ... }
 */
export function getNodeLabels(nodeType: string): SchemaProperty {
  return getNodeProperties(nodeType);
}

// ==================== 关系操作 ====================

/**
 * 获取所有关系类型
 */
export function getAllRelationshipTypes(): Record<string, SchemaRelationshipType> {
  return getSchema().relationshipTypes;
}

/**
 * 获取单个关系类型定义
 */
export function getRelationshipType(relType: string): SchemaRelationshipType | undefined {
  return getSchema().relationshipTypes[relType];
}

/**
 * 获取某节点类型的所有出边关系
 * @param nodeType 节点类型名
 * 返回: 关系类型数组
 */
export function getOutgoingRelations(nodeType: string): string[] {
  return Object.entries(getSchema().relationshipTypes)
    .filter(([_, rel]) => rel.from === nodeType)
    .map(([key]) => key);
}

/**
 * 获取某节点类型的所有入边关系
 */
export function getIncomingRelations(nodeType: string): string[] {
  return Object.entries(getSchema().relationshipTypes)
    .filter(([_, rel]) => rel.to === nodeType)
    .map(([key]) => key);
}

// ==================== 查询模板 ====================

/**
 * 获取预定义的 Cypher 查询模板
 */
export function getQueryTemplates(): Record<string, string> {
  return getSchema().queryTemplates;
}

/**
 * 获取单个查询模板
 */
export function getQueryTemplate(name: string): string | undefined {
  return getSchema().queryTemplates[name];
}

// ==================== 动态 API 获取（生产模式） ====================

/**
 * 从后端 API 获取 Schema（生产环境使用）
 * 用法: const schema = await fetchSchemaFromAPI();
 */
export async function fetchSchemaFromAPI(baseUrl = ''): Promise<SchemaDefinition> {
  const response = await fetch(`${baseUrl}/api/schema`);
  if (!response.ok) {
    throw new Error(`Failed to fetch schema: ${response.status}`);
  }
  return response.json();
}

/**
 * 从后端 API 获取节点类型
 */
export async function fetchNodeTypesFromAPI(baseUrl = ''): Promise<Record<string, SchemaNodeType>> {
  const response = await fetch(`${baseUrl}/api/schema/node-types`);
  if (!response.ok) {
    throw new Error(`Failed to fetch node types: ${response.status}`);
  }
  return response.json();
}

/**
 * 从后端 API 获取关系类型
 */
export async function fetchRelationshipsFromAPI(baseUrl = ''): Promise<Record<string, SchemaRelationshipType>> {
  const response = await fetch(`${baseUrl}/api/schema/relationships`);
  if (!response.ok) {
    throw new Error(`Failed to fetch relationships: ${response.status}`);
  }
  return response.json();
}
