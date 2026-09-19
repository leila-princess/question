import { NextResponse } from "next/server";
import axios from "axios";

export async function POST(request: Request) {
  try {
    // 从请求体中获取参数
    const requestBody = await request.json();
    console.log("POST接收到的请求体:", requestBody);
    // 构建Cypher查询和参数
     const actualParams = requestBody.params || requestBody;
    
    // 从正确的位置解构参数
    const { 
      keyword = '', 
      nodeType = '', 
      relationshipType = '', 
      limit = 50 
    } = actualParams;

    // 构建Cypher查询
    const query = `
      MATCH (start)-[r]-(end) WHERE toLower(start.name) CONTAINS toLower($keyword) OR toLower(end.name) CONTAINS toLower($keyword)
      ${nodeType && nodeType !== "all" ? `AND any(label IN labels(start) WHERE label = $nodeType) OR any(label IN labels(end) WHERE label = $nodeType)` 
        : ""}
      ${relationshipType ? `AND type(r) = $relationshipType` : ""} RETURN start, r, end LIMIT $limit
    `;

    const params = {
      keyword: keyword.toLowerCase(),
      nodeType: nodeType !== "all" ? nodeType : undefined,
      relationshipType,
      limit
    };

    console.log("发送到后端的查询:", { query, params });
    
    // 发送请求到后端API
    const response = await fetch("http://localhost:5000/api/kg/all_nodes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit }),
    });

    // 检查响应状态
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    // 解析JSON响应
    const data = await response.json();
    
    // 返回统一格式的响应
    return NextResponse.json({
      success: data.success,
      nodes: data.nodes || [],
      edges: data.edges || [],
      count: data.count || { nodes: 0, edges: 0 },
      message: data.message || "查询成功"
    });
  } catch (error: any) {
    console.error("查询数据库出错:", error);
    // 返回统一格式的错误响应
    return NextResponse.json({
      success: false,
      nodes: [],
      edges: [],
      count: { nodes: 0, edges: 0 },
      message: error.message || "查询数据库时发生未知错误"
    }, { status: 500 });
  }
}