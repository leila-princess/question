# SSYQ（语义搜索查询）前端说明文档

## 页面路径
`fronted/app/GLCX/SSYQ/page.tsx`

## 页面功能概述
语义搜索查询页面，用户输入关键词后，搜索设备/项目/指标，以表格和知识图谱两种形式展示结果，并根据选择的项目/指标关联查询试验方法。

## 页面布局
```
┌─────────────────────────────────────────────────┐
│  搜索栏 (Card)                                   │
│  [输入框] [检索按钮] [重置按钮]                   │
├─────────────────────────────────────────────────┤
│  搜索结果表格 (Card)                              │
│  使用 EnvironmentTable 组件                       │
│  显示设备在各环境下的项目(单选)和指标(单选)         │
├──────────────────────┬──────────────────────────┤
│  知识图谱 (flex: 1)   │  方法列表 (固定360px宽)    │
│  KnowledgeGraph组件   │  固定高度500px             │
│  高度100%自适应        │  内部overflowY: auto滚动   │
│  显示节点关联关系      │  可收缩(留32px+展开按钮)    │
└──────────────────────┴──────────────────────────┘
```

## 核心组件引用

### 1. EnvironmentTable (`@/components/EnvironmentTable`)
- **作用**：中间表格，显示设备在各环境下的项目和指标
- **Props**：
  - `dataSource`: `EnvironmentTableRow[]` 表格数据
  - `selectedProjects` / `selectedIndicators`: 已选项
  - `onProjectSelect` / `onIndicatorSelect`: 选择回调
  - `singleSelect`: `true` 单选模式
  - `scrollY`: `"calc(100vh - 360px)"` 表格滚动高度
- **单选逻辑**：点击新项目/指标时，先清除同设备类型下所有已选项，再选中新项

### 2. KnowledgeGraph (`@/components/KnowledgeGraph/KnowledgeGraph`)
- **作用**：知识图谱可视化
- **Props**：
  - `initialData`: `{ nodes, edges }` 图谱数据
  - `nodeColors` / `edgeColors`: 颜色映射
  - `nodeLabels` / `edgeLabels`: 标签映射
  - `height`: `"100%"` 自适应父容器高度
  - `showLegend`: `true` 显示图例
  - `showControls`: `true` 显示控制按钮

### 3. MethodList (`@/components/MethodList`)
- **作用**：方法卡片列表
- **Props**：
  - `methods`: 方法数组
  - `selectedMethodIds`: 已选方法ID
  - `onMethodSelect`: 选择回调
  - `singleSelect`: `true` 单选
  - 按相关度排序（both > project-only > indicator-only）

## 数据流

### 搜索流程
```
用户输入关键词
    ↓
fetchSearch(keyword) → POST /api/GLCX/SSYQ?path=/search
    ↓
后端返回 { tableRows, graphData }
    ↓
前端解析 tableRows → 转换为 EnvironmentTableRow[]
    ↓
setTableRows(envRows) → 表格渲染
setGraphData(graphData) → 图谱渲染
```

### 方法查询流程
```
用户在表格中选择项目/指标
    ↓
selectedProjects / selectedIndicators 变化
    ↓
useEffect 监听变化 (300ms 防抖)
    ↓
fetchMethods({ projectIds, indicatorIds }) → POST /api/GLCX/SSYQ?path=/methods
    ↓
setFilteredMethods(methods) → MethodList 渲染
```

## 状态管理
| 状态 | 类型 | 说明 |
|------|------|------|
| `searchKeyword` | string | 搜索关键词 |
| `searchLoading` | boolean | 搜索加载状态 |
| `tableRows` | EnvironmentTableRow[] | 表格数据 |
| `graphData` | SearchGraphData | 图谱数据 |
| `selectedProjects` | SelectedProjectEntry[] | 已选项目 |
| `selectedIndicators` | SelectedIndicatorEntry[] | 已选指标 |
| `filteredMethods` | MethodNode[] | 过滤后的方法 |
| `selectedMethodIds` | string[] | 已选方法ID |
| `methodsLoading` | boolean | 方法加载状态 |
| `methodCollapsed` | boolean | 方法栏折叠状态 |
| `hasSearched` | useRef | 是否已搜索（控制下半部分显示） |

## 数据转换逻辑
后端返回的 `tableRows` 中 `type: "equipment"` 的行直接映射为 `EnvironmentTableRow`：
```typescript
envRows.push({
  equipmentTypeId: row.equipmentId,
  equipmentTypeName: row.equipmentName,
  projectsByEnv: row.projectsByEnv || {},  // 直接透传
  indicators: row.indicators || [],         // 直接透传
});
```

`type: "project"` 或 `type: "indicator"` 的行，通过 `equipments` 字段提取设备信息，合并到对应设备的行中。

## 环境列固定顺序
使用 `@/constants/environments.ts` 中的 `ENVIRONMENT_ORDER` 常量：
`['复杂电磁环境', '复杂地理环境', '复杂气象环境', '复杂水文环境', '复杂水声环境', '高原综合环境', '海上综合环境', '其他环节']`

## 方法栏布局
- 固定宽度 360px
- 固定高度 500px
- 内部 `overflowY: auto` 实现滚动
- 左侧有收缩按钮，收起后留 32px 宽度 + 展开按钮
