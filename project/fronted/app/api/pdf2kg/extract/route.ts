import { NextResponse } from "next/server";

export async function POST(request: Request) {
  try {
    // 从请求中获取 FormData
    const formData = await request.formData();

    // 检查是否包含文件
    const file = formData.get("file");
    if (!file) {
      return NextResponse.json(
        { success: false, message: "未提供文件" },
        { status: 400 }
      );
    }

    // 转发到后端Python服务
    const backendUrl = process.env.BACKEND_URL || "http://localhost:5000";
    const response = await fetch(`${backendUrl}//api/pdf2kg/extract`, {
      method: "POST",
      body: formData, // 直接转发 FormData
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`后端返回错误: ${response.status} ${errorText}`);
    }

    const data = await response.json();

    // 直接返回后端的JSON响应（包含 extraction 和 session_id）
    return NextResponse.json(data, { status: response.status });
  } catch (error: any) {
    console.error("提取失败:", error);
    return NextResponse.json(
      { success: false, message: error.message || "提取过程中发生错误" },
      { status: 500 }
    );
  }
}