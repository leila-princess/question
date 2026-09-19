import { NextResponse } from "next/server";

export async function POST(request: Request) {
  try {
    const requestBody = await request.json();

    const { session_id } = requestBody;
    if (!session_id) {
      return NextResponse.json(
        { success: false, message: "缺少 session_id" },
        { status: 400 }
      );
    }

    const backendUrl = process.env.BACKEND_URL || "http://localhost:5000";
    const response = await fetch(`${backendUrl}/api/pdf2kg/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`后端返回错误: ${response.status} ${errorText}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error: any) {
    console.error("确认导入失败:", error);
    return NextResponse.json(
      { success: false, message: error.message || "确认导入过程中发生错误" },
      { status: 500 }
    );
  }
}