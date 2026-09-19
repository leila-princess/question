// app/api/pdf2kg/save/route.ts
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  try {
    // 解析前端传来的 JSON 数据（包含 nodes 和 relationships）
    const requestBody = await request.json();

    // 基本验证：必须包含 nodes 和 relationships
    if (!requestBody.nodes || !requestBody.relationships) {
      return NextResponse.json(
        { success: false, message: "缺少 nodes 或 relationships 字段" },
        { status: 400 }
      );
    }

    // 后端服务地址（从环境变量读取，默认本地开发地址）
    const backendUrl = process.env.BACKEND_URL || "http://localhost:5000";

    // 转发到后端的 /api/kg/save 接口
    const response = await fetch(`${backendUrl}/api/pdf2kg/save`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        nodes: requestBody.nodes,
        relationships: requestBody.relationships,
      }),
    });

    // 检查后端响应
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`后端返回错误: ${response.status} ${errorText}`);
    }

    const data = await response.json();
    // 直接返回后端的 JSON 响应（通常包含 status 和 message）
    return NextResponse.json(data, { status: response.status });
  } catch (error: any) {
    console.error("保存数据失败:", error);
    return NextResponse.json(
      { success: false, message: error.message || "保存过程中发生错误" },
      { status: 500 }
    );
  }
}