// app/api/experiment/answer/route.ts
import { NextRequest, NextResponse } from "next/server";

export interface SubmitAnswerRequest {
  sessionId: string;
  answers: Array<{
    questionId: string;
    answer: string;
  }>;
}

export interface SubmitAnswerResponse {
  success: boolean;
  action: "next_chapter" | "complete";
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
  progress?: {
    current: number;
    total: number;
  };
  preview?: string;
  downloadUrl?: string;
  reportInfo?: {
    title: string;
    domain: string;
    chapterCount: number;
  };
  error?: string;
}

export async function POST(req: NextRequest) {
  try {
    const { sessionId, answers } = await req.json() as SubmitAnswerRequest;
    
    if (!sessionId || !answers?.length) {
      return NextResponse.json(
        { success: false, error: "缺少必要参数: sessionId 或 answers" },
        { status: 400 }
      );
    }

    // 转换答案格式适配后端
    const backendAnswers = answers.reduce((acc, curr) => {
      acc[curr.questionId] = curr.answer;
      return acc;
    }, {} as Record<string, string>);

    const backendResponse = await fetch("http://localhost:5000/api/experiment/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        sessionId, 
        answers: backendAnswers 
      })
    });

    const backendData = await backendResponse.json();

    if (!backendData.success) {
      return NextResponse.json(
        { success: false, error: backendData.error || "后端处理答案失败" },
        { status: 500 }
      );
    }

    const response: SubmitAnswerResponse = {
      success: true,
      action: backendData.action,
      questions: backendData.questions?.map((q: string, i: number) => ({
        id: `q${i+1}`,
        text: q,
        type: "text"
      })),
      currentChapter: backendData.currentChapter || {
        id: `chapter_${backendData.currentChapterIdx || 0}`,
        title: backendData.chapterTitle || "默认章节",
        level: 1
      },
      progress: {
        current: backendData.currentChapter || 1,
        total: backendData.totalChapters || 1
      },
      preview : backendData.preview,
      downloadUrl: backendData.downloadUrl,
      reportInfo: backendData.reportInfo ? {
        title: backendData.reportInfo.title || "试验报告",
        domain: backendData.reportInfo.domain || "",
        chapterCount: backendData.reportInfo.chapterCount || 0
      } : undefined
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