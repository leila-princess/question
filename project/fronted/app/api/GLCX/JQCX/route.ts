import { NextRequest, NextResponse } from 'next/server';

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';
const API_PREFIX = '/api/GLCX/JQCX';

/**
 * Next.js API Route — 代理到 Flask 后端
 * 所有 /api/GLCX/JQCX/* 请求转发到 Flask 的 /api/glcx/jqcx/*
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get('path') || '';
  const query = searchParams;
  query.delete('path');

  const queryString = query.toString();
  const url = `${BACKEND_URL}${API_PREFIX}${path}${queryString ? '?' + queryString : ''}`;

  try {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(30000),
    });

    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('image/') || contentType.includes('application/octet-stream')) {
      const buffer = await res.arrayBuffer();
      return new NextResponse(buffer, {
        status: res.status,
        headers: { 'Content-Type': contentType },
      });
    }

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (error: any) {
    console.error(`API 请求失败: ${path}`, error);
    return NextResponse.json(
      { success: false, error: error.name === 'TimeoutError' ? '请求超时' : 'Backend connection failed' },
      { status: 502 }
    );
  }
}

export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get('path') || '';
  const body = await request.json();

  const url = `${BACKEND_URL}${API_PREFIX}${path}`;

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30000), // 30秒超时
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (error: any) {
    console.error(`API 请求失败: ${path}`, error);
    return NextResponse.json(
      { success: false, error: error.name === 'TimeoutError' ? '请求超时' : 'Backend connection failed' },
      { status: 502 }
    );
  }
}
