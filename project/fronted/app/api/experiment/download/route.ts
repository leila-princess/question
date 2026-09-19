// app/api/experiment/download/route.ts
import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const sessionId = searchParams.get('sessionId');
    const filename = searchParams.get('filename');
    
    if (!sessionId && !filename) {
      return NextResponse.json(
        { success: false, error: "缺少参数" },
        { status: 400 }
      );
    }

    let downloadUrl = '';
    
    if (filename) {
      // 直接使用文件名下载
      downloadUrl = `http://localhost:5000/api/download/report?file=${filename}`;
    } else if (sessionId) {
      // 先获取会话信息获取文件名
      const sessionResponse = await fetch(`http://localhost:5000/api/experiment/session/${sessionId}`);
      if (!sessionResponse.ok) {
        return NextResponse.json(
          { success: false, error: "会话不存在" },
          { status: 404 }
        );
      }
      
      const sessionData = await sessionResponse.json();
      const downloadPath = sessionData.session?.state?.download_url;
      
      if (!downloadPath) {
        return NextResponse.json(
          { success: false, error: "文件尚未生成" },
          { status: 404 }
        );
      }
      
      downloadUrl = `http://localhost:5000${downloadPath}`;
    }

    // 转发到后端下载
    const response = await fetch(downloadUrl);
    
    if (!response.ok) {
      return NextResponse.json(
        { success: false, error: "文件下载失败" },
        { status: 500 }
      );
    }

    // 获取文件内容和类型
    const fileContent = await response.arrayBuffer();
    const contentType = response.headers.get('content-type') || 'application/octet-stream';
    const contentDisposition = response.headers.get('content-disposition') || `attachment; filename="${filename || 'report.docx'}"`;

    return new NextResponse(fileContent, {
      headers: {
        'Content-Type': contentType,
        'Content-Disposition': contentDisposition,
      },
    });
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : "未知错误";
    return NextResponse.json(
      { success: false, error: errorMessage },
      { status: 500 }
    );
  }
}