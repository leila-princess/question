"""
ZNWD2 意图消歧铁律（对齐 ZNWD1 routing_disambiguation.py，供意图解析提示词注入）。
"""

DISAMBIGUATION_RULES_MARKDOWN = """
## 消歧铁律（必须遵守）

### 图谱硬约束
1. **考核/性能/试验指标（Indicator）仅依赖设备（Equipment）**：路径为 Equipment -CON-> Indicator（一步直连）。
   - **禁止**经 TestProject 查指标；指标不是项目的子对象。
   - 问句含环境词但目的是「有哪些指标」时，仍只按设备查指标，**environment_constraint 必须为 null**。
2. **试验项目（TestProject）同时依赖设备与环境**：
   - 仅问「某设备有哪些项目」→ Equipment -CON-> TestProject。
   - 问「某设备在某环境下有哪些项目」→ Equipment 与 Environment **共同约束** TestProject（双锚点/EnvironmentId）。
3. **试验方法（TestMethod）** 经 CON 一步相连的只有 TestProject、Indicator：
   - 设备/环境**不能**与 TestMethod 一步 CON。
   - 设备→方法：`Equipment→TestProject→TestMethod`（默认）或 `Equipment→Indicator→TestMethod`（问法含指标/考核/性能时）。
   - 环境→方法：须经项目 `Environment→TestProject→TestMethod`（两步）。
4. **环境属性** Type/Class/Combination/Limit/Request 只读 **Environment** 节点，禁止用 TestProject.Type 代替环境分类。
5. 关系类型仅 HAS、CON、BELONGS_TO；方向与 schema 一致。

### intent_type 与环境槽位
| intent_type | environment_constraint | 说明 |
|-------------|------------------------|------|
| **列举** | **必须 null** | 全库列举某类节点 |
| **查关联** | null | 如 EquipmentType -HAS-> Equipment |
| 查指标 | **必须 null** | 只填 start_filters（具体设备名） |
| 查项目 | 有环境词则填 Environment 过滤 | 设备 + 环境双约束 |
| 查方法 | 有环境词则填 Environment 过滤 | 经项目查方法 |
| 查文件 | 通常 null | 从 TestMethod 查 File |

### 易混问法
- 「**有哪些设备类型**」→ **列举**，target_entities=["EquipmentType"]（**不是查指标**）
- 「有哪些试验环境」→ **列举**，target_entities=["Environment"]
- 「无人机有哪些考核指标」→ 查指标，start_filters={Name:无人机}，environment=null
- 「高原环境下无人机有哪些试验项目」→ 查项目，environment={Type:高原} 或 {Name:高原}
- 「高温下无人机用什么试验方法」→ 查方法，environment 填环境，additional_filters={route:via_project}
- 「按指标查某设备的试验方法」→ 查方法，additional_filters={route:via_indicator}
"""
