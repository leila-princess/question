import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const qs = url.searchParams.toString();
    const response = await fetch(
      `${BACKEND_URL}/api/znwd6/entities/enum${qs ? `?${qs}` : ""}`,
      { cache: "no-store" }
    );
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("[znwd6/entities/enum proxy] Error:", error);
    return NextResponse.json({ error: "Proxy failed" }, { status: 500 });
  }
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 200 });
}
