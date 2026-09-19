import { NextResponse } from "next/server";
import axios from "axios";

export async function POST(request: Request) {
  try {
    // 从请求体中获取参数
    const requestBody = await request.json();
    
    // 构建请求参数，设置默认值
    const requestParams = {
      keyword: requestBody.keyword || '',
      nodeType: requestBody.nodeType || '',
      relationshipType: requestBody.relationshipType || '',
      source: requestBody.source || '',
      limit: requestBody.limit || 100,
    };

    console.log("发送到后端的参数:", requestParams);
    
    // 发送请求到后端API
    const response = await fetch("http://localhost:5000/api/kg/proxy", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestParams),
    });

    // 检查响应状态
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    // 解析JSON响应
    const backendData = await response.json();
    // 适配后端返回的格式
    console.log("后端返回的原始数据:", backendData);
    // 返回统一格式的响应
    return NextResponse.json({
      success: true,
      data: {
      nodes: backendData.nodes || [],
      edges: backendData.edges || [],
      count: backendData.count || { nodes: 0, edges: 0 }
      },
      total: backendData.count ? (backendData.count.nodes + backendData.count.edges) : 0,
      message: backendData.message
    });
  } catch (error: any) {
    console.error("查询数据库出错:", error);
    // 返回统一格式的错误响应
    return NextResponse.json({
      success: false,
      data: { nodes: [], edges: [], count: { nodes: 0, edges: 0 } },
      message: error.message || "查询数据库时发生未知错误"
    }, { status: 500 });
  }
  }
