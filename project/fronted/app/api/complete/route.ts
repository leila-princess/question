// app/api/experiment/start/route.ts
import { NextRequest, NextResponse } from "next/server";

export interface StartExperimentRequest {
  domain: string;
  sections: string; // 完整文档内容
  metadata?: {
    title?: string;
    standard_code?: string;
  };
  userinfo:string;
}

export interface StartExperimentResponse {
  success: boolean;
  sessionId?: string;
  questions?: Array<{
    id: string;
    text: string;
    type?: string;
  }>;
  currentChapter?: {
    id: string;
    title: string;
    level: number;
    tables?: string[];
    figures?: string[];
  };
  metadata?: {
    title: string;
    standard_code?: string;
    scope?: string;
  };
  progress?: {
    current: number;
    total: number;
  };
  error?: string;
}

export async function POST(req: NextRequest) {
  try {
    const { domain, sections, metadata, userinfo } = await req.json() as StartExperimentRequest;
    
    if (!domain || !sections) {
      return NextResponse.json(
        { success: false, error: "缺少必要参数: domain 或 sections" },
        { status: 400 }
      );
    }

    const backendResponse = await fetch("http://localhost:5000/api/experiment/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        domain,
        sections,
        metadata: metadata || {},
        userinfo: userinfo || {}
      })
    });

    const backendData = await backendResponse.json();

    if (!backendData.success) {
      return NextResponse.json(
        { success: false, error: backendData.error || "后端服务启动失败" },
        { status: 500 }
      );
    }

    const response: StartExperimentResponse = {
      success: true,
      sessionId: backendData.sessionId,
      questions: backendData.questions?.map((q: string, i: number) => ({
        id: `q${i+1}`,
        text: q,
        type: "text"
      })),
      currentChapter: backendData.currentChapter || {
        id: `chapter_0`,
        title: backendData.chapterTitle || "默认章节",
        level: 1
      },
      metadata: backendData.metadata,
      progress: backendData.progress || {
        current: 1,
        total: backendData.totalChapters || 1
      }
    };

    return NextResponse.json(response);
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : "未知错误";
    return NextResponse.json(
      { success: false, error: errorMessage },
      { status: 500 }
    );
  }
}