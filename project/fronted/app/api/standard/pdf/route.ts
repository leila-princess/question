import { NextRequest, NextResponse } from 'next/server';
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const url = `${BACKEND_URL}/api/standard/pdf/${searchParams.get('id') || ''}`;
  try {
    const res = await fetch(url);
    const blob = await res.blob();
    return new NextResponse(blob, { status: res.status, headers: { 'Content-Type': 'application/pdf' } });
  } catch {
    return NextResponse.json({ error: 'Backend connection failed' }, { status: 502 });
  }
}
