import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const response = await fetch(`${BACKEND_URL}/api/fzsj/jyllm/build_skeleton`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        { success: false, error: `Backend error: ${response.status}`, detail: text },
        { status: response.status },
      );
    }
    try {
      return NextResponse.json(JSON.parse(text));
    } catch {
      return NextResponse.json({ success: false, error: "Invalid JSON from backend", detail: text }, { status: 502 });
    }
  } catch (error: any) {
    return NextResponse.json({ success: false, error: error.message || "Proxy failed" }, { status: 500 });
  }
}
