import { NextResponse } from "next/server";

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    const file = formData.get("file");
    if (!file) {
      return NextResponse.json(
        { success: false, message: "未提供文件" },
        { status: 400 }
      );
    }

    const backendUrl = process.env.BACKEND_URL || "http://localhost:5000";
    const response = await fetch(`${backendUrl}/api/pdf2kg/ocr-extract`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`后端返回错误: ${response.status} ${errorText}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error: any) {
    console.error("OCR提取失败:", error);
    return NextResponse.json(
      { success: false, message: error.message || "OCR提取过程中发生错误" },
      { status: 500 }
    );
  }
}
