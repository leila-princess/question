import { NextResponse } from "next/server";
import axios from "axios";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const standardId = searchParams.get('standardId');
  
  if (!standardId) {
    return NextResponse.json(
      { error: "缺少standardId参数" },
      { status: 400 }
    );
  }

  try {
    const res = await axios.get(`http://localhost:5000/standard/summary?standardId=${encodeURIComponent(standardId)}`);
    return NextResponse.json(res.data);
  } catch (err) {
    return NextResponse.json({ error: "请求后端失败" }, { status: 500 });
  }
}
