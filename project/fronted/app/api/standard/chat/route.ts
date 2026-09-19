import { NextRequest, NextResponse } from 'next/server';
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';
export async function POST(request: NextRequest) {
  const body = await request.json();
  try {
    const res = await fetch(`${BACKEND_URL}/api/standard/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch { return NextResponse.json({ success: false, error: 'Backend connection failed' }, { status: 502 }); }
}
