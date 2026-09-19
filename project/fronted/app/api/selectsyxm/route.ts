import { NextResponse } from "next/server";
import axios from "axios";

export async function POST(request: Request) {
  try {
    // 从请求体中获取参数
    const requestBody = await request.json();
    
    // 构建请求参数，设置默认值
    const requestParams = {
      testItemId: requestBody.testItemId || '',
      queryType: requestBody.queryType || '',
      name: requestBody.name || '',
      limit: requestBody.limit || 100,
    };

    console.log("发送到后端的参数:", requestParams);
    
    // 发送请求到后端API
    const response = await fetch("http://localhost:5000/api/kg/query_by_standard", {
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
    const data = await response.json();
    
    // 返回统一格式的响应
   return NextResponse.json({
    success: data.success,
    record: data.data,
  });

  } catch (error: any) {
    console.error("查询数据库出错:", error);
    // 返回统一格式的错误响应
    return NextResponse.json({
      success: false,
      data: [],
      message: error.message || "查询数据库时发生未知错误"
    }, { status: 500 });
  }
}