import { NextResponse } from "next/server";
const BACKEND = process.env.BACKEND_URL || "http://localhost:5000";

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    const res = await fetch(`${BACKEND}/api/zstq/extract`, { method: "POST", body: formData });
    if (!res.ok) throw new Error(await res.text());
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
