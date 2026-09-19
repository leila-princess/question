// app/api/experiment/stream-start/route.ts
import { NextRequest, NextResponse } from "next/server";

export interface StreamStartRequest {
  domain: string;
  sections: string;
  metadata?: {
    title?: string;
    standard_code?: string;
  };
  userinfo?: Record<string, any>;
}

export interface StreamStartChunk {
  type: 'chunk' | 'complete' | 'error';
  content?: string;
  sessionId?: string;
  thinkingLog?: Array<{
    timestamp: string;
    content: string;
    type: 'analysis' | 'reasoning' | 'conclusion';
  }>;
  questions?: Array<{
    id: string;
    text: string;
    type: 'text' | 'choice' | 'number';
    options?: string[];
    priority: 'high' | 'medium' | 'low';
    reasoning?: string;
  }>;
}

export async function POST(req: NextRequest) {
  try {
    const { domain, sections, metadata, userinfo } = await req.json() as StreamStartRequest;
    
    if (!domain || !sections) {
      return NextResponse.json(
        { success: false, error: "缺少必要参数: domain 或 sections" },
        { status: 400 }
      );
    }

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        try {
          // 连接后端流式API
          const backendResponse = await fetch("http://localhost:5000/api/experiment/stream-start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              domain,
              sections,
              metadata: metadata || {},
              userinfo: userinfo || {}
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
          let sessionId = '';

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
                    // 转发思考内容
                    controller.enqueue(
                      encoder.encode(`data: ${JSON.stringify({
                        type: 'chunk',
                        content: data.chunk
                      })}\n\n`)
                    );
                  }
                  
                  if (data.sessionId) {
                    sessionId = data.sessionId;
                  }
                  
                  if (data.complete) {
                    // 获取完整分析结果
                    const sessionResponse = await fetch(`http://localhost:5000/api/experiment/session/${sessionId}`);
                    if (sessionResponse.ok) {
                      const sessionData = await sessionResponse.json();
                      
                      controller.enqueue(
                        encoder.encode(`data: ${JSON.stringify({
                          type: 'complete',
                          sessionId: sessionId,
                          thinkingLog: sessionData.session?.state?.thinking_log || [],
                          questions: sessionData.session?.state?.questions || [],
                          draft: sessionData.session?.state?.first_draft || ''
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
          console.error('流式启动错误:', error);
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