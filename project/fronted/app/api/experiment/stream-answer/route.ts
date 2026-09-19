// app/api/experiment/stream-answer/route.ts
import { NextRequest, NextResponse } from "next/server";

export interface StreamAnswerRequest {
  sessionId: string;
  questionId: string;
  answer: string;
}

export interface StreamAnswerChunk {
  type: 'chunk' | 'complete' | 'error';
  content?: string;
  questions?: Array<{
    id: string;
    text: string;
    type: 'text' | 'choice' | 'number';
    options?: string[];
    priority: 'high' | 'medium' | 'low';
    reasoning?: string;
  }>;
  draft?: string;
  status?: {
    completion_percentage: number;
    is_complete: boolean;
    download_url?: string;
  };
}

export async function POST(req: NextRequest) {
  try {
    const { sessionId, questionId, answer } = await req.json() as StreamAnswerRequest;
    
    if (!sessionId || !questionId || !answer) {
      return NextResponse.json(
        { success: false, error: "缺少必要参数" },
        { status: 400 }
      );
    }

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        try {
          // 连接后端流式回答API
          const backendResponse = await fetch("http://localhost:5000/api/experiment/stream-answer", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              sessionId,
              questionId,
              answer
            })
          });

          if (!backendResponse.ok) {
            const errorData = await backendResponse.json();
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify({
                type: 'error',
                content: `后端错误: ${errorData.error || backendResponse.statusText}`
              })}\n\n`)
            );
            controller.close();
            return;
          }

          if (!backendResponse.body) {
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify({
                type: 'error',
                content: '后端响应体为空'
              })}\n\n`)
            );
            controller.close();
            return;
          }

          // 读取后端流式响应
          const reader = backendResponse.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let accumulatedContent = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6));
                  
                  if (data.chunk) {
                    accumulatedContent += data.chunk;
                    // 转发思考内容
                    controller.enqueue(
                      encoder.encode(`data: ${JSON.stringify({
                        type: 'chunk',
                        content: data.chunk
                      })}\n\n`)
                    );
                  }
                  
                  if (data.complete) {
                    // 获取更新后的会话状态
                    const sessionResponse = await fetch(`http://localhost:5000/api/experiment/session/${sessionId}`);
                    if (sessionResponse.ok) {
                      const sessionData = await sessionResponse.json();
                      
                      controller.enqueue(
                        encoder.encode(`data: ${JSON.stringify({
                          type: 'complete',
                          isFinal: data.isFinal || false,
                          questions: sessionData.session?.state?.questions || [],
                          draft: sessionData.session?.state?.draft_versions?.[sessionData.session?.state?.draft_versions?.length - 1]?.content || '',
                          status: {
                            completion_percentage: sessionData.session?.state?.completion_percentage || 0,
                            is_complete: sessionData.session?.state?.completed || false,
                            download_url: sessionData.session?.state?.download_url
                          }
                        })}\n\n`)
                      );
                    }
                    controller.close();
                    return;
                  }
                } catch (e) {
                  console.error('解析流式数据错误:', e);
                }
              }
            }
          }

          controller.close();
        } catch (error) {
          console.error('流式回答错误:', error);
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify({
              type: 'error',
              content: error instanceof Error ? error.message : '未知错误'
            })}\n\n`)
          );
          controller.close();
        }
      }
    });

    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
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