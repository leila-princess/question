# fix-slow-pages.ps1
Write-Host "🔧 修复慢速页面..." -ForegroundColor Green

# 需要修复的页面
$slowPages = @(
    "app\qa\page.tsx",
    "app\select\page.tsx", 
    "app\Div_Management\page.tsx",
    "app\experimentsc\page.tsx",
    "app\knowledge_management\page.tsx",
    "app\settings\page.tsx",
    "app\visual_management\page.tsx"
)

foreach ($pagePath in $slowPages) {
    if (Test-Path $pagePath) {
        Write-Host "检查: $pagePath" -ForegroundColor Yellow
        
        $content = Get-Content $pagePath -Raw
        
        # 备份原文件
        $backupPath = $pagePath + ".backup"
        Copy-Item -Path $pagePath -Destination $backupPath -Force
        
        # 1. 确保有 "use client"
        if (-not $content.Contains("'use client'") -and -not $content.Contains('"use client"')) {
            $content = "'use client';`n`n" + $content
        }
        
        # 2. 简化大型导入（示例）
        # 如果有大型组件导入，可以在这里处理
        
        # 保存
        if ($content -ne (Get-Content $backupPath -Raw)) {
            $content | Out-File -FilePath $pagePath -Encoding UTF8
            Write-Host "  ✅ 已修复" -ForegroundColor Green
        }
    }
}

Write-Host "✅ 页面检查完成" -ForegroundColor Green
