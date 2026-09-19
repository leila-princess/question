export interface Node {
  id: string;
  labels: string[];
  properties: {
    name: string;
    title?: string;
    description?: string;
    source?: string;
    createdAt?: string;
    updatedAt?: string;
    [key: string]: any;
  };
}

export interface Relationship {
  id: string;
  type: string;
  startNodeId: string;
  endNodeId: string;
  properties: {
    [key: string]: any;
  };
}

export interface GraphData {
  nodes: Node[];
  relationships: Relationship[];
}

export interface SearchResult {
  node: Node;
  relationships?: Relationship[]; // 相关关系
}