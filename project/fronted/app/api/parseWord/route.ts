import { NextResponse } from "next/server";
import * as JSZip from "jszip";
import { parseStringPromise } from "xml2js";
import mammoth from "mammoth";

/**
 * 从 docx XML 中提取表格
 */
async function extractTablesFromDocx(buffer: Buffer) {
  const zip = await JSZip.loadAsync(buffer);
  const documentXml = await zip.file("word/document.xml")?.async("string");
  if (!documentXml) return [];

  const xml = await parseStringPromise(documentXml);
  const tables: string[][][] = [];
  const tblArray = xml["w:document"]["w:body"][0]["w:tbl"] || [];

  for (const tbl of tblArray) {
    const rows: string[][] = [];
    const trArray = tbl["w:tr"] || [];
    for (const tr of trArray) {
      const cells: string[] = [];
      const tcArray = tr["w:tc"] || [];
      for (const tc of tcArray) {
        const texts: string[] = [];
        const paras = tc["w:p"] || [];
        for (const p of paras) {
          const runs = p["w:r"] || [];
          for (const r of runs) {
            const tArr = r["w:t"] || [];
            for (const t of tArr) {
              if (typeof t === "string") {
                texts.push(t);
              } else if (t._) {
                texts.push(t._);
              }
            }
          }
        }
        cells.push(texts.join(""));
      }
      rows.push(cells);
    }
    tables.push(rows);
  }
  return tables;
}

/**
 * POST 方法 - 解析 Word 文件
 */
export async function POST(req: Request) {
  try {
    // 从请求中获取 FormData
    const formData = await req.formData();
    const file = formData.get("file") as File | null;

    if (!file) {
      return NextResponse.json({ success: false, error: "缺少 file 字段" }, { status: 400 });
    }

    if (!file.name.endsWith(".docx")) {
      return NextResponse.json({ success: false, error: "只支持 .docx 文件" }, { status: 400 });
    }

    // File -> Buffer
    const arrayBuffer = await file.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);

    // 1. 提取正文
    const result = await mammoth.extractRawText({ buffer });
    const lines = result.value
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean);

    // 目录提取（示例规则：包含“试验/测试/项目/章节/方案”的行）
    const toc = lines.filter((l) => /试验|测试|项目|章节|方案/.test(l));

    // 2. 提取表格
    const tables = await extractTablesFromDocx(buffer);

    return NextResponse.json({
      success: true,
      title: lines[0] || "未识别标题",
      toc,
      preview: lines.slice(0, 20), // 前 20 行
      fullTextLength: result.value.length,
      tables, // 三维数组
    });
  } catch (err: any) {
    console.error("Word 解析错误:", err);
    return NextResponse.json({ success: false, error: err.message }, { status: 500 });
  }
}
