const fs = require('fs');
const path = require('path');
const { mdToPdf } = require('md-to-pdf');
const { PDFDocument } = require('pdf-lib');

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

(async () => {
  const inputPath = process.argv[2];
  const outputPath = process.argv[3];
  const docName = process.argv[4] || '试验方案';

  if (!inputPath || !outputPath) {
    console.error('Usage: node convert.js <input.md> <output.pdf> [docName]');
    process.exit(1);
  }

  const stylesheet = [path.join(__dirname, 'style.css')];
  const launch_options = {
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  };
  const chromeWin = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  if (fs.existsSync(chromeWin)) {
    launch_options.executablePath = chromeWin;
  }

  /** 页眉：XXX试验方案（若 docName 已含「试验方案」则不再重复拼接） */
  const headerLine = String(docName).includes('试验方案') ? String(docName) : `${docName}试验方案`;
  /** 页眉/页脚印在 Puppeteer margin 区域内；正文四边 2cm 留白 */
  const headerTemplate = `<div style="width:100%;box-sizing:border-box;padding:0 2mm 2px;font-size:8.5pt;font-family:SimSun,'宋体',serif;text-align:center;border-bottom:0.5px solid #bdbdbd;color:#222;">${escapeHtml(headerLine)}</div>`;
  const footerTemplate = `<div style="width:100%;box-sizing:border-box;padding:2px 2mm 0;font-size:8.5pt;font-family:SimSun,'宋体',serif;text-align:center;border-top:0.5px solid #bdbdbd;color:#444;"><span class="pageNumber"></span> / <span class="totalPages"></span></div>`;

  /** 每页四边 2cm（Puppeteer page.pdf margin；勿与 style.css 中 @page margin:0 叠加冲突） */
  const marginAll = { top: '2cm', bottom: '2cm', left: '2cm', right: '2cm' };

  const tmp1 = outputPath.replace(/\.pdf$/i, '_cover.pdf');
  const tmp2 = outputPath.replace(/\.pdf$/i, '_body.pdf');

  try {
    // 第 1 页：无页眉/页脚，四边 2cm
    await mdToPdf(
      { path: inputPath },
      {
        dest: tmp1,
        document_title: headerLine,
        stylesheet,
        launch_options,
        pdf_options: {
          format: 'A4',
          printBackground: true,
          preferCSSPageSize: false,
          displayHeaderFooter: false,
          pageRanges: '1',
          margin: { ...marginAll },
        },
      },
    );

    let hasBody = false;
    try {
      await mdToPdf(
        { path: inputPath },
        {
          dest: tmp2,
          document_title: headerLine,
          stylesheet,
          launch_options,
          pdf_options: {
            format: 'A4',
            printBackground: true,
            preferCSSPageSize: false,
            displayHeaderFooter: true,
            headerTemplate,
            footerTemplate,
            pageRanges: '2-',
            margin: { ...marginAll },
          },
        },
      );
      const st = fs.statSync(tmp2);
      hasBody = st.size > 100;
    } catch (e) {
      console.warn('Second pass (page 2-) skipped:', e.message);
      hasBody = false;
    }

    if (!hasBody) {
      fs.copyFileSync(tmp1, outputPath);
      try {
        fs.unlinkSync(tmp1);
      } catch (_) {}
      try {
        if (fs.existsSync(tmp2)) fs.unlinkSync(tmp2);
      } catch (_) {}
      console.log('PDF generated (single page):', outputPath);
      return;
    }

    const merged = await PDFDocument.create();
    const coverBytes = fs.readFileSync(tmp1);
    const bodyBytes = fs.readFileSync(tmp2);
    const coverDoc = await PDFDocument.load(coverBytes);
    const bodyDoc = await PDFDocument.load(bodyBytes);

    const [coverPage] = await merged.copyPages(coverDoc, [0]);
    merged.addPage(coverPage);

    const nBody = bodyDoc.getPageCount();
    if (nBody === 0) {
      const outBytesSingle = await merged.save();
      fs.writeFileSync(outputPath, outBytesSingle);
      try {
        fs.unlinkSync(tmp1);
      } catch (_) {}
      try {
        fs.unlinkSync(tmp2);
      } catch (_) {}
      console.log('PDF generated (cover only):', outputPath);
      return;
    }

    for (let i = 0; i < nBody; i += 1) {
      const [p] = await merged.copyPages(bodyDoc, [i]);
      merged.addPage(p);
    }

    const outBytes = await merged.save();
    fs.writeFileSync(outputPath, outBytes);

    try {
      fs.unlinkSync(tmp1);
    } catch (_) {}
    try {
      fs.unlinkSync(tmp2);
    } catch (_) {}

    console.log('PDF generated successfully:', outputPath);
  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
