import { NextRequest, NextResponse } from 'next/server';

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';
const API_PREFIX = '/api/FLGL';

// ========== 新增：判断是否是文件上传请求 ==========
function isMultipartRequest(request: NextRequest): boolean {
  const contentType = request.headers.get('content-type') || '';
  return contentType.includes('multipart/form-data');
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get('path') || '';
  const query = searchParams;
  query.delete('path');
  const queryString = query.toString();
  const url = `${BACKEND_URL}${API_PREFIX}${path}${queryString ? '?' + queryString : ''}`;

  try {
    const res = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ success: false, error: 'Backend connection failed' }, { status: 502 });
  }
}

// ========== 修改：POST 函数，增加文件上传处理 ==========
export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get('path') || '';
  
  // 新增：判断是否是文件上传请求
  if (isMultipartRequest(request)) {
    try {
      // 获取上传的 FormData
      const formData = await request.formData();
      const url = `${BACKEND_URL}${API_PREFIX}${path}`;
      
      // 转发到后端
      const res = await fetch(url, {
        method: 'POST',
        body: formData,  // 直接传递 formData，不设置 Content-Type（让浏览器自动设置）
      });
      
      const data = await res.json();
      return NextResponse.json(data, { status: res.status });
    } catch (error) {
      console.error('File upload proxy error:', error);
      return NextResponse.json(
        { success: false, error: 'File upload failed' },
        { status: 502 }
      );
    }
  }
  
  // 原有的 JSON 请求处理
  try {
    const body = await request.json();
    const url = `${BACKEND_URL}${API_PREFIX}${path}`;
    
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ success: false, error: 'Backend connection failed' }, { status: 502 });
  }
}

//export async function POST(request: NextRequest) {
//  const { searchParams } = new URL(request.url);
//  const path = searchParams.get('path') || '';
//  const body = await request.json();
//  const url = `${BACKEND_URL}${API_PREFIX}${path}`;

//  try {
//    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
//    const data = await res.json();
//    return NextResponse.json(data, { status: res.status });
//  } catch {
//    return NextResponse.json({ success: false, error: 'Backend connection failed' }, { status: 502 });
//  }
//}

export async function PUT(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get('path') || '';
  const body = await request.json();
  const url = `${BACKEND_URL}${API_PREFIX}${path}`;

  try {
    const res = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ success: false, error: 'Backend connection failed' }, { status: 502 });
  }
}

export async function DELETE(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get('path') || '';
  const query = searchParams;
  query.delete('path');
  const queryString = query.toString();
  const url = `${BACKEND_URL}${API_PREFIX}${path}${queryString ? '?' + queryString : ''}`;

  try {
    const res = await fetch(url, { method: 'DELETE' });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ success: false, error: 'Backend connection failed' }, { status: 502 });
  }
}
