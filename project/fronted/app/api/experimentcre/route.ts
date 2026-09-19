import { NextResponse } from "next/server";
import axios from "axios";

export async function POST(req: Request) {
  const data = await req.json();
  try {
    const res = await axios.post("http://localhost:5000/api/fa/createfa", data);
    return NextResponse.json(res.data);
  } catch (err) {
    return NextResponse.json({ error: "请求后端失败" }, { status: 500 });
  }
}



