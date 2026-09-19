import { message } from 'antd';

// utils/exportUtils.ts
export interface ExportContent {
  title: string;
  equipmentType: string;
  testItemCount: number;
  methodCount: number;
  content: string;
  indicatorCount?: number;
}

// 格式化文本内容，识别标题、表格等标记
export const formatContent = (content: string) => {
  if (!content) return { html: "", markdown: "", plainText: "" };
  
  // 解析标题
  let htmlContent = content
    .replace(/^# (.+)$/gm, '<h1 style="font-size: 24px; font-weight: bold; margin: 16px 0 8px 0; color: #1890ff;">$1</h1>')
    .replace(/^## (.+)$/gm, '<h2 style="font-size: 20px; font-weight: bold; margin: 14px 0 8px 0; color: #333;">$1</h2>')
    .replace(/^### (.+)$/gm, '<h3 style="font-size: 18px; font-weight: bold; margin: 12px 0 6px 0; color: #333;">$1</h3>')
    .replace(/^#### (.+)$/gm, '<h4 style="font-size: 16px; font-weight: bold; margin: 10px 0 6px 0; color: #333;">$1</h4>')
    .replace(/^##### (.+)$/gm, '<h5 style="font-size: 14px; font-weight: bold; margin: 8px 0 4px 0; color: #333;">$1</h5>')
    .replace(/^###### (.+)$/gm, '<h6 style="font-size: 12px; font-weight: bold; margin: 6px 0 4px 0; color: #333;">$1</h6>');
  
  // 解析粗体和斜体
  htmlContent = htmlContent
    .replace(/\*\*(.+?)\*\*/g, '<strong style="font-weight: bold;">$1</strong>')
    .replace(/\*(.+?)\*/g, '<em style="font-style: italic;">$1</em>');
  
  // 解析列表
  htmlContent = htmlContent
    .replace(/^- (.+)$/gm, '<li style="margin: 4px 0; padding-left: 8px;">$1</li>')
    .replace(/(<li style="margin: 4px 0; padding-left: 8px;">.*?<\/li>)+/g, match => `<ul style="margin: 8px 0; padding-left: 20px;">${match}</ul>`);
  
  // 解析表格
  htmlContent = htmlContent.replace(/\|(.+?)\|/g, (match, p1) => {
    const rows = p1.split('\n').filter((row: string) => row.trim());
    
    if (rows.length < 2) return match; // 不是有效的表格
    
    let tableHtml = '<table style="width: 100%; border-collapse: collapse; margin: 12px 0;">';
    
    rows.forEach((row: string, index: number) => {
      const cells = row.split('|').filter((cell: string) => cell.trim());
      
      if (index === 0) {
        // 表头
        tableHtml += '<thead><tr style="background-color: #f0f8ff;">';
        cells.forEach((cell: string) => {
          tableHtml += `<th style="border: 1px solid #ddd; padding: 8px; text-align: left; font-weight: bold;">${cell.trim()}</th>`;
        });
        tableHtml += '</tr></thead><tbody>';
      } else {
        // 表格内容
        tableHtml += '<tr>';
        cells.forEach((cell: string) => {
          tableHtml += `<td style="border: 1px solid #ddd; padding: 8px;">${cell.trim()}</td>`;
        });
        tableHtml += '</tr>';
      }
    });
    
    tableHtml += '</tbody></table>';
    return tableHtml;
  });
  
  // 处理普通段落
  htmlContent = htmlContent
    .replace(/(<h[1-6].*?<\/h[1-6]>)|(<table.*?<\/table>)|(<ul.*?<\/ul>)|(<li.*?<\/li>)/g, match => match) // 保留已处理的元素
    .replace(/([^<>]+)(?=(<h[1-6]|<\/h[1-6]|<table|<\/table|<ul|<\/ul|<li|<\/li|$))/g, '<p style="margin: 8px 0; line-height: 1.6;">$1</p>')
    .replace(/<p style="margin: 8px 0; line-height: 1.6;"><\/p>/g, '') // 移除空段落
    .replace(/\n/g, '<br/>');
  
  // 创建Markdown版本（保留原始格式）
  const markdownContent = content;
  
  return {
    html: htmlContent,
    markdown: markdownContent,
    plainText: content.replace(/\n\s*\n/g, '\n\n') // 规范化换行
  };
};

// 导出PDF函数
// export const exportToPDF = async (content: ExportContent) => {
//   const { jsPDF } = await import('jspdf');
  
//   // 创建PDF实例
//   const pdf = new jsPDF();
  
//   // 设置中文字体 - 使用支持中文的字体
//   pdf.addFont('https://cdn.jsdelivr.net/npm/@fonts/source-han-sans-simplified-chinese@4.0.0/WOFF2/HanSansSC-VF.ttf', 'HanSansSC', 'normal');
//   pdf.setFont('HanSansSC');
//   pdf.setFontSize(12);
  
//   const pageWidth = pdf.internal.pageSize.getWidth();
//   const margin = 15;
//   const lineHeight = 7;
//   let y = margin;
  
//   // 添加标题
//   pdf.setFontSize(16);
//   pdf.text(`试验方案 - ${content.title}`, pageWidth / 2, y, { align: 'center' });
//   y += lineHeight * 2;
  
//   // 添加基本信息
//   pdf.setFontSize(12);
//   pdf.text(`装备类型: ${content.equipmentType}`, margin, y);
//   y += lineHeight;
//   pdf.text(`试验项目数: ${content.testItemCount}`, margin, y);
//   y += lineHeight;
//   pdf.text(`试验方法数: ${content.methodCount}`, margin, y);
//   y += lineHeight * 2;
  
//   // 添加详细方案内容
//   pdf.setFontSize(10);
  
//   // 处理内容中的格式
//   const formattedContent = formatContent(content.content);
  
//   // 分割文本以适应PDF页面
//   const lines = pdf.splitTextToSize(formattedContent.plainText, pageWidth - 2 * margin);
  
//   for (let i = 0; i < lines.length; i++) {
//     if (y > pdf.internal.pageSize.getHeight() - margin) {
//       pdf.addPage();
//       y = margin;
//     }
//     pdf.text(lines[i], margin, y);
//     y += lineHeight;
//   }
  
//   pdf.save(`试验方案_${content.equipmentType}_${new Date().getTime()}.pdf`);
//   return true;
// };


export const exportToPDF = async (content: ExportContent): Promise<boolean> => {
  try {
    message.info('正在生成PDF...');
    
    // 使用html2canvas和jspdf组合
    const html2canvas = await import('html2canvas');
    const { jsPDF } = await import('jspdf');
    
    // A4页面尺寸（毫米）
    const A4_WIDTH_MM = 210;
    const A4_HEIGHT_MM = 297;
    
    // 转换为像素（96 DPI）
    const MM_TO_PX = 3.78;
    const PAGE_WIDTH_PX = A4_WIDTH_MM * MM_TO_PX;  // 794px
    const PAGE_HEIGHT_PX = A4_HEIGHT_MM * MM_TO_PX; // 1123px
    
    // 页边距设置（毫米）
    const MARGIN_TOP_MM = 20;
    const MARGIN_BOTTOM_MM = 20;
    const MARGIN_LEFT_MM = 20;
    const MARGIN_RIGHT_MM = 20;
    
    // 内容区域（毫米）
    const CONTENT_WIDTH_MM = A4_WIDTH_MM - MARGIN_LEFT_MM - MARGIN_RIGHT_MM; // 170mm
    const CONTENT_HEIGHT_MM = A4_HEIGHT_MM - MARGIN_TOP_MM - MARGIN_BOTTOM_MM; // 257mm
    
    // 转换为像素
    const CONTENT_WIDTH_PX = CONTENT_WIDTH_MM * MM_TO_PX; // 约642px
    const CONTENT_HEIGHT_PX = CONTENT_HEIGHT_MM * MM_TO_PX; // 约971px
    
    // 创建一个临时的div来渲染内容
    const element = document.createElement('div');
    element.style.position = 'absolute';
    element.style.left = '-9999px';
    element.style.width = `${CONTENT_WIDTH_PX}px`;
    element.style.maxWidth = `${CONTENT_WIDTH_PX}px`;
    element.style.fontFamily = 'SimSun, STSong, "宋体", sans-serif';
    element.style.fontSize = '12px'; // 减小字体大小
    element.style.lineHeight = '1.6'; // 调整行高
    element.style.color = '#000000';
    element.style.boxSizing = 'border-box';
    element.style.padding = '0';
    element.style.margin = '0';
    element.style.overflow = 'hidden';
    element.style.wordWrap = 'break-word';
    element.style.wordBreak = 'break-all';
    
    // 改进的Markdown转HTML函数，严格控制样式
    const markdownToHtml = (markdown: string): string => {
      let html = markdown;
      
      // 清除多余的空格和换行
      html = html.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
      
      // 处理标题 - 严格控制样式
      html = html.replace(/^# (.*)$/gm, 
        '<h1 style="font-size: 16px; font-weight: bold; margin: 15px 0 10px 0; text-align: center; page-break-before: always; word-break: break-all;">$1</h1>');
      
      html = html.replace(/^## (.*)$/gm, 
        '<h2 style="font-size: 15px; font-weight: bold; margin: 12px 0 8px 0; border-left: 3px solid #1890ff; padding-left: 8px; page-break-after: avoid; word-break: break-all;">$1</h2>');
      
      html = html.replace(/^### (.*)$/gm, 
        '<h3 style="font-size: 14px; font-weight: bold; margin: 10px 0 6px 0; word-break: break-all;">$1</h3>');
      
      html = html.replace(/^#### (.*)$/gm, 
        '<h4 style="font-size: 13px; font-weight: bold; margin: 8px 0 5px 0; word-break: break-all;">$1</h4>');
      
      // 处理粗体
      html = html.replace(/\*\*(.*?)\*\*/g, 
        '<strong style="font-weight: bold;">$1</strong>');
      
      // 处理斜体
      html = html.replace(/\*(.*?)\*/g, 
        '<em style="font-style: italic;">$1</em>');
      
      // 处理代码块 - 严格控制宽度
      html = html.replace(/```([\s\S]*?)```/g, 
        '<div style="background: #f6f8fa; padding: 10px; border-radius: 3px; border: 1px solid #e1e4e8; font-family: Consolas, Monaco, monospace; font-size: 11px; margin: 10px 0; overflow-x: auto; max-width: 100%; box-sizing: border-box;"><code style="white-space: pre-wrap; word-break: break-all;">$1</code></div>');
      
      // 处理行内代码
      html = html.replace(/`([^`]+)`/g, 
        '<code style="background: #f6f8fa; padding: 1px 3px; border-radius: 2px; font-family: Consolas, Monaco, monospace; font-size: 11px; border: 1px solid #e1e4e8; word-break: break-all; display: inline;">$1</code>');
      
      // 处理列表
      const lines = html.split('\n');
      let result = [];
      let inList = false;
      let listType = ''; // 'ul' 或 'ol'
      let listItems = [];
      
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        
        if (!line) {
          if (inList && listItems.length > 0) {
            // 结束当前列表
            result.push(`<${listType} style="margin: 8px 0; padding-left: 15px; max-width: 100%;">`);
            result.push(...listItems);
            result.push(`</${listType}>`);
            inList = false;
            listItems = [];
            listType = '';
          }
          result.push('');
          continue;
        }
        
        // 检查无序列表
        if (line.match(/^[-*+]\s+/)) {
          const content = line.replace(/^[-*+]\s+/, '');
          if (!inList || listType !== 'ul') {
            if (inList && listItems.length > 0) {
              result.push(`<${listType} style="margin: 8px 0; padding-left: 15px; max-width: 100%;">`);
              result.push(...listItems);
              result.push(`</${listType}>`);
            }
            listType = 'ul';
            listItems = [];
            inList = true;
          }
          listItems.push(`<li style="margin: 3px 0; word-break: break-all; max-width: 100%;">${content}</li>`);
        }
        // 检查有序列表
        else if (line.match(/^\d+\.\s+/)) {
          const content = line.replace(/^\d+\.\s+/, '');
          if (!inList || listType !== 'ol') {
            if (inList && listItems.length > 0) {
              result.push(`<${listType} style="margin: 8px 0; padding-left: 15px; max-width: 100%;">`);
              result.push(...listItems);
              result.push(`</${listType}>`);
            }
            listType = 'ol';
            listItems = [];
            inList = true;
          }
          listItems.push(`<li style="margin: 3px 0; word-break: break-all; max-width: 100%;">${content}</li>`);
        }
        // 普通文本
        else {
          if (inList && listItems.length > 0) {
            result.push(`<${listType} style="margin: 8px 0; padding-left: 15px; max-width: 100%;">`);
            result.push(...listItems);
            result.push(`</${listType}>`);
            inList = false;
            listItems = [];
            listType = '';
          }
          
          // 检查是否已经是HTML标签
          if (line.startsWith('<h') || line.startsWith('<div') || line.startsWith('<code') || line.startsWith('<pre')) {
            result.push(line);
          } else {
            // 普通段落
            result.push(`<p style="margin: 8px 0; text-indent: 2em; line-height: 1.6; word-break: break-all; max-width: 100%;">${line}</p>`);
          }
        }
      }
      
      // 处理最后一个列表
      if (inList && listItems.length > 0) {
        result.push(`<${listType} style="margin: 8px 0; padding-left: 15px; max-width: 100%;">`);
        result.push(...listItems);
        result.push(`</${listType}>`);
      }
      
      html = result.join('\n');
      
      // 处理表格
      if (html.includes('|')) {
        const tableRegex = /(\|.*\|[\s\S]*?)(?=\n\n|\n#|\n$)/g;
        html = html.replace(tableRegex, (tableMatch) => {
          const rows = tableMatch.trim().split('\n').filter(row => row.trim());
          if (rows.length < 2) return tableMatch;
          
          let tableHtml = '<div style="margin: 10px 0; overflow-x: auto; max-width: 100%;">';
          tableHtml += '<table style="width: 100%; border-collapse: collapse; border: 1px solid #ddd; font-size: 11px; table-layout: fixed;">';
          
          rows.forEach((row, rowIndex) => {
            const cells = row.split('|').filter(cell => cell.trim());
            if (cells.length === 0) return;
            
            tableHtml += '<tr>';
            cells.forEach(cell => {
              const cellContent = cell.trim();
              if (rowIndex === 0) {
                // 表头
                tableHtml += `<th style="border: 1px solid #ddd; padding: 5px; text-align: center; background: #f5f5f5; font-weight: bold; word-break: break-all;">${cellContent}</th>`;
              } else if (rowIndex === 1 && cellContent.includes('---')) {
                // 分隔行 - 跳过
              } else {
                // 数据行
                tableHtml += `<td style="border: 1px solid #ddd; padding: 5px; text-align: left; word-break: break-all;">${cellContent}</td>`;
              }
            });
            tableHtml += '</tr>';
          });
          
          tableHtml += '</table></div>';
          return tableHtml;
        });
      }
      
      return html;
    };
    
    // 构建HTML内容 - 严格控制样式
    const htmlContent = `
      <div style="width: ${CONTENT_WIDTH_PX}px; max-width: ${CONTENT_WIDTH_PX}px; margin: 0; padding: 0; box-sizing: border-box; font-family: SimSun, STSong, '宋体', sans-serif; font-size: 12px; line-height: 1.6; color: #000;">
        <!-- 封面页 -->
        <div style="page-break-after: always; min-height: ${CONTENT_HEIGHT_PX}px; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center;">
          <h1 style="font-size: 18px; font-weight: bold; margin: 0 0 30px 0; color: #1890ff; word-break: break-all;">
            试验方案报告
          </h1>
          <h2 style="font-size: 16px; margin: 0 0 40px 0; font-weight: bold; word-break: break-all;">
            ${content.title}
          </h2>
          <div style="text-align: left; font-size: 13px; width: 80%;">
            <p style="margin: 8px 0;"><strong>装备类型：</strong>${content.equipmentType}</p>
            <p style="margin: 8px 0;"><strong>试验项目数：</strong>${content.testItemCount}</p>
            <p style="margin: 8px 0;"><strong>试验方法数：</strong>${content.methodCount}</p>
            ${content.indicatorCount ? `<p style="margin: 8px 0;"><strong>指标数：</strong>${content.indicatorCount}</p>` : ''}
            <p style="margin: 8px 0;"><strong>生成时间：</strong>${new Date().toLocaleString('zh-CN')}</p>
          </div>
        </div>
        
        <!-- 详细内容 -->
        <div style="page-break-before: always;">
          ${markdownToHtml(content.content)}
        </div>
      </div>
    `;
    
    element.innerHTML = htmlContent;
    document.body.appendChild(element);
    
    // 等待DOM渲染
    await new Promise(resolve => setTimeout(resolve, 200));
    
    // 创建PDF
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4',
      compress: true
    });
    
    // 计算总页数
    const contentHeight = element.scrollHeight;
    const pages = Math.ceil(contentHeight / CONTENT_HEIGHT_PX);
    
    // 逐页生成
    for (let page = 0; page < pages; page++) {
      if (page > 0) {
        pdf.addPage();
      }
      
      // 设置当前页的偏移
      element.style.top = `-${page * CONTENT_HEIGHT_PX}px`;
      
      const canvas = await html2canvas.default(element, {
        scale: 1.5, // 降低分辨率以加快速度
        useCORS: true,
        logging: false,
        backgroundColor: '#ffffff',
        windowHeight: CONTENT_HEIGHT_PX,
        height: CONTENT_HEIGHT_PX,
        width: CONTENT_WIDTH_PX,
        y: page * CONTENT_HEIGHT_PX,
        scrollX: 0,
        scrollY: -page * CONTENT_HEIGHT_PX,
        onclone: (clonedDoc) => {
          // 确保克隆的元素也有正确的样式
          const div = clonedDoc.querySelector('div');
          if (div) {
            div.style.position = 'relative';
            div.style.top = `-${page * CONTENT_HEIGHT_PX}px`;
          }
        }
      });
      
      // 将canvas添加到PDF，考虑页边距
      const imgData = canvas.toDataURL('image/png', 0.9);
      pdf.addImage(
        imgData, 
        'PNG', 
        MARGIN_LEFT_MM, 
        MARGIN_TOP_MM, 
        CONTENT_WIDTH_MM, 
        CONTENT_HEIGHT_MM
      );
    }
    
    // 移除临时元素
    document.body.removeChild(element);
    
    const filename = `试验方案_${content.equipmentType}_${new Date().getTime()}.pdf`;
    pdf.save(filename);
    
    message.success('PDF导出成功');
    return true;
    
  } catch (error) {
    console.error('PDF导出失败:', error);
    
    // 备选方案：使用纯文本方式，确保能导出
    try {
      message.info('正在使用简化方案生成PDF...');
      
      const { jsPDF } = await import('jspdf');
      const pdf = new jsPDF();
      
      // 页面设置
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 15;
      const lineHeight = 5;
      let y = margin;
      
      // 封面
      pdf.setFontSize(14);
      pdf.text('试验方案报告', pageWidth / 2, y, { align: 'center' });
      y += 10;
      
      pdf.text(content.title, pageWidth / 2, y, { align: 'center' });
      y += 15;
      
      pdf.setFontSize(10);
      pdf.text(`装备类型: ${content.equipmentType}`, margin, y);
      y += lineHeight;
      pdf.text(`试验项目数: ${content.testItemCount}`, margin, y);
      y += lineHeight;
      pdf.text(`试验方法数: ${content.methodCount}`, margin, y);
      y += lineHeight;
      if (content.indicatorCount) {
        pdf.text(`指标数: ${content.indicatorCount}`, margin, y);
        y += lineHeight;
      }
      pdf.text(`生成时间: ${new Date().toLocaleString('zh-CN')}`, margin, y);
      
      // 添加新页
      pdf.addPage();
      y = margin;
      
      // 处理内容 - 简单去除markdown标记
      const cleanContent = content.content
        .replace(/#{1,6}\s+/g, '')
        .replace(/\*\*/g, '')
        .replace(/\*/g, '')
        .replace(/`/g, '')
        .replace(/\[.*?\]\(.*?\)/g, '')
        .trim();
      
      // 分割文本
      const lines = pdf.splitTextToSize(cleanContent, pageWidth - 2 * margin);
      
      pdf.setFontSize(9); // 更小的字体
      for (const line of lines) {
        if (y > pageHeight - margin) {
          pdf.addPage();
          y = margin;
        }
        pdf.text(line, margin, y);
        y += lineHeight;
      }
      
      const filename = `试验方案_${content.equipmentType}_${new Date().getTime()}.pdf`;
      pdf.save(filename);
      
      message.success('PDF导出成功（简化版）');
      return true;
      
    } catch (fallbackError) {
      console.error('备选方案失败:', fallbackError);
      message.error('导出失败，请检查控制台');
      return false;
    }
  }
};


export function markdownToHtml(markdown: string): string {
  let html = markdown;
  
  // 处理标题
  html = html.replace(/^# (.*$)/gim, '<h1 style="font-size: 22px; font-weight: bold; margin: 30px 0 15px 0; text-align: center;">$1</h1>');
  html = html.replace(/^## (.*$)/gim, '<h2 style="font-size: 18px; font-weight: bold; margin: 25px 0 12px 0; border-left: 4px solid #1890ff; padding-left: 10px;">$1</h2>');
  html = html.replace(/^### (.*$)/gim, '<h3 style="font-size: 16px; font-weight: bold; margin: 20px 0 10px 0;">$1</h3>');
  html = html.replace(/^#### (.*$)/gim, '<h4 style="font-size: 15px; font-weight: bold; margin: 18px 0 9px 0;">$1</h4>');
  
  // 处理粗体
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="font-weight: bold;">$1</strong>');
  html = html.replace(/__(.*?)__/g, '<strong style="font-weight: bold;">$1</strong>');
  
  // 处理斜体
  html = html.replace(/\*(.*?)\*/g, '<em style="font-style: italic;">$1</em>');
  html = html.replace(/_(.*?)_/g, '<em style="font-style: italic;">$1</em>');
  
  // 处理代码块
  html = html.replace(/```([\s\S]*?)```/g, '<pre style="background: #f6f8fa; padding: 15px; border-radius: 5px; border: 1px solid #e1e4e8; font-family: Consolas, Monaco, monospace; font-size: 13px; overflow-x: auto; margin: 15px 0;"><code>$1</code></pre>');
  
  // 处理行内代码
  html = html.replace(/`([^`]+)`/g, '<code style="background: #f6f8fa; padding: 2px 5px; border-radius: 3px; font-family: Consolas, Monaco, monospace; font-size: 13px; border: 1px solid #e1e4e8;">$1</code>');
  
  // 处理无序列表
  let lines = html.split('\n');
  let result = [];
  let inUl = false;
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    
    // 检查是否是无序列表项
    if (line.match(/^\s*[-*+]\s+/)) {
      if (!inUl) {
        result.push('<ul style="margin: 15px 0; padding-left: 20px;">');
        inUl = true;
      }
      const itemContent = line.replace(/^\s*[-*+]\s+/, '');
      result.push(`<li style="margin: 5px 0; padding-left: 20px; text-indent: -20px;">• ${itemContent}</li>`);
    } else {
      if (inUl) {
        result.push('</ul>');
        inUl = false;
      }
      result.push(line);
    }
  }
  
  // 关闭最后一个列表
  if (inUl) {
    result.push('</ul>');
  }
  
  html = result.join('\n');
  
  // 处理有序列表
  lines = html.split('\n');
  result = [];
  let inOl = false;
  let olCounter = 1;
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    
    // 检查是否是有序列表项
    const olMatch = line.match(/^\s*(\d+)\.\s+(.*)/);
    if (olMatch) {
      if (!inOl) {
        result.push('<ol style="margin: 15px 0; padding-left: 20px;">');
        inOl = true;
        olCounter = 1;
      }
      const itemContent = olMatch[2];
      result.push(`<li style="margin: 5px 0; padding-left: 20px; text-indent: -20px;">${olCounter}. ${itemContent}</li>`);
      olCounter++;
    } else {
      if (inOl) {
        result.push('</ol>');
        inOl = false;
      }
      result.push(line);
    }
  }
  
  // 关闭最后一个有序列表
  if (inOl) {
    result.push('</ol>');
  }
  
  html = result.join('\n');
  
  // 处理段落
  const paragraphs = html.split('\n\n');
  html = paragraphs.map(p => {
    const trimmed = p.trim();
    if (!trimmed) return '';
    
    // 检查是否已经是HTML标签
    if (trimmed.startsWith('<') && trimmed.endsWith('>')) {
      return trimmed;
    }
    
    // 检查是否是列表项（已经处理过）
    if (trimmed.startsWith('<li>') || trimmed.startsWith('<ul>') || trimmed.startsWith('<ol>')) {
      return trimmed;
    }
    
    // 普通段落
    return `<p style="margin: 12px 0; text-indent: 2em; line-height: 1.8;">${trimmed.replace(/\n/g, '<br>')}</p>`;
  }).filter(p => p).join('\n');
  
  return html;
}
// 导出Word函数
export const exportToWord = async (content: ExportContent) => {
  // 格式化内容
  const formattedContent = formatContent(content.content);
  
  // 创建HTML内容
  const htmlContent = `
    <html xmlns:o="urn:schemas-microsoft-com:office:office" 
          xmlns:w="urn:schemas-microsoft-com:office:word" 
          xmlns="http://www.w3.org/TR/REC-html40">
    <head>
      <meta charset="utf-8">
      <title>试验方案</title>
      <style>
        body { font-family: SimSun, serif; margin: 20px; }
        h1 { color: #1890ff; border-bottom: 1px solid #ddd; padding-bottom: 10px; font-size: 24px; }
        h2 { color: #333; margin-top: 20px; font-size: 20px; }
        h3 { font-size: 18px; }
        h4 { font-size: 16px; }
        table { border-collapse: collapse; width: 100%; margin: 10px 0; }
        table, th, td { border: 1px solid #ddd; }
        th { background-color: #f0f8ff; font-weight: bold; }
        th, td { padding: 8px; text-align: left; }
        ul { margin: 10px 0; padding-left: 20px; }
        li { margin: 5px 0; }
        strong { font-weight: bold; }
        em { font-style: italic; }
        p { margin: 8px 0; line-height: 1.6; }
      </style>
    </head>
    <body>
      <h1>试验方案 - ${content.title}</h1>
      <p><strong>装备类型:</strong> ${content.equipmentType}</p>
      <p><strong>试验项目数:</strong> ${content.testItemCount}</p>
      <p><strong>试验方法数:</strong> ${content.methodCount}</p>
      <hr>
      <div>${formattedContent.html}</div>
    </body>
    </html>
  `;
  
  // 创建Blob对象
  const blob = new Blob([htmlContent], { type: 'application/msword' });
  
  // 创建下载链接
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `试验方案_${content.equipmentType}_${new Date().getTime()}.doc`;
  
  // 触发下载
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  
  return true;
};

// 导出Markdown函数
export const exportToMarkdown = async (content: ExportContent) => {
  // 格式化内容
  const formattedContent = formatContent(content.content);
  
  // 创建Markdown内容
  const markdownContent = `# 试验方案 - ${content.title}
  
**装备类型:** ${content.equipmentType}

**试验项目数:** ${content.testItemCount}

**试验方法数:** ${content.methodCount}

---

${formattedContent.markdown}
`;
  
  // 创建Blob对象
  const blob = new Blob([markdownContent], { type: 'text/markdown;charset=utf-8' });
  
  // 创建下载链接
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `试验方案_${content.equipmentType}_${new Date().getTime()}.md`;
  
  // 触发下载
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  
  return true;
};