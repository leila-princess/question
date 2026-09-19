"""
ZNWD1 路由消歧铁律：汇总 rel_inference / method_inference / plan_reconcile / 旧版 ZNWD 规则。
仅用于生成 routing_prompt_builder 静态核，执行期仍以 plan_reconcile guardrail 为准。
"""

from __future__ import annotations

# Markdown 块，注入路由 LLM system prompt
DISAMBIGUATION_RULES_MARKDOWN = """
## 消歧铁律（必须遵守，冲突时以本条为准）

### 0. 清单库优先（总原则）
- **凡涉及设备/环境/项目/方法/指标/标准/清单库**，必须先查 Neo4j 或 Chroma，**禁止** answer_strategy=direct_llm 凭空编造（如 HR 绩效、OKR 等域外内容）。
- 仅当图库与文档库均无相关命中、且确认为域外常识时，才可 db_synthesize 或极少量 direct_llm 说明「库中未找到」。
- 用户问法模糊（如「考核指标呢」「指标呢」）→ 对应图节点 **Indicator**；无具体设备名时默认 **enum** 列举指标，有上文设备则 **Equipment→Indicator**。

### A. 目的节点（用户想得到什么类型的答案）
1. 「盐雾/高温/振动…**环境**下有哪些**项目**」→ purpose=**TestProject**，known=**Environment**（不是 Environment.enum）
2. 「**该项目**在什么/哪个**环境**」→ purpose=**Environment**，known=**TestProject**（反查）
3. 「**设备/装备**有哪些**试验/测试项目**」→ purpose=**TestProject**，known=**Equipment**
4. 「**设备类型/类别**下有哪些**具体设备/装备**」→ purpose=**Equipment**，known=**EquipmentType**，关系 HAS（不是列举全部设备类型 enum）
5. 「**设备**有哪些**考核/性能/试验指标**」→ purpose=**Indicator**，known=**Equipment**（**禁止**经 TestProject 查指标）
6. 「**项目**有哪些**试验方法**」→ purpose=**TestMethod**，known=**TestProject**（仅列方法名，非正文）
7. 「**指标**关联/有哪些**方法**」→ purpose=**TestMethod**，known=**Indicator**
8. 「**方法**来源于哪份**标准/文件**」→ purpose=**File**，known=**TestMethod**
9. 「**标准**包含哪些**方法**」→ purpose=**TestMethod**，known=**File**

### B. biz_type 与文档/正文
10. 问方法**正文/内容/步骤/怎么做/SYNR** → biz_type=**node_prop**，purpose=**TestMethod**（**不是** document）
11. 问「有哪些方法」仅列名称 → hop_n 或 node_kw，**不是** node_prop 正文
12. 问 **GB/T、GJB、标准第 X 章/节、条文** 且问**条文内容** → biz_type=**document**（Chroma），**不是** File.node_kw
13. 问「**哪些设备/项目**具备/有 **某某标准/方法编号**」→ hop_n 反查设备+项目（**不是** document）

### C. 图谱硬约束（禁止臆造）
14. 关系类型仅 **HAS、CON、BELONGS_TO**；方向与本体表一致
15. TestMethod 经 **CON** 一步相连的只有 **TestProject、Indicator**；设备/环境**不能**与 TestMethod 一步 CON
16. 设备→方法：经项目 `Equipment→TestProject→TestMethod` 或经指标 `Equipment→Indicator→TestMethod`；问法含「指标/考核」走指标路径
17. 环境字段 Type/Class/Combination/Limit/Request 只读 **Environment** 节点，**禁止**用 TestProject.Type 代替环境分类
18. focus_id / method_id **仅**来自 probe_snapshot，**禁止编造**

### D. 与 enumerate / node_kw 区分
19. 「有哪些**设备类型**/试验环境/指标类型」且无具体实体名 → biz_type=**enum**
20. 「查/找**名叫 XX** 的某类节点」→ biz_type=**node_kw**，field 默认 Name
21. 「Type=category / Type=陆地 / Class=Ⅲ」筛节点 → biz_type=**node_prop_rev**，填 field+value

### E. 多装备 / 多关键词拆分
25. 问句含**两个及以上具体装备**（「A和B」「A、B」「A与B」「分别」）且同一环境/条件 → `equipment_keywords` **数组**逐项列出；`environment_keywords` 共用环境；scenario 选 `by_equip_and_env`（或对应业务场景），**不要**只填第一个装备。
26. `keyword` / `focus` 优先填**第一个装备名**，勿用环境名顶替 equipment_keywords。

### F. 续问与闲聊
22. 「它/上述/该设备/继续」→ use_history=true，先调 history_recent 再填 focus/keyword
23. 「上一个/刚才/之前的问题是什么」→ use_history=true，先调 **history_recent**；answer_strategy=**direct_llm**；答句须引用历史中**最近一轮用户问句**
24. 打招呼/闲聊 → answer_strategy=**direct_llm**，不 probe、不查库

### G. 检索结果呈现（程序执行，非路由字段）
27. Chroma 有命中 → answer_strategy=**db_synthesize**（禁止直出文档片段列表）
28. TestMethod 图库有实质 SYNR → 优先 database_direct 直出正文

### H. 环境设置推理（拼装业务）
29. 「**某装备**做/进行**某项目**时，**环境**具体设置/应该如何/分类/分级/组合/约束/要求是什么」→ 选拼装业务 `COMP_EQUIP_PROJECT_ENV_SETTINGS`，填 `equipment_keywords` + `project_keywords`
30. 典型问法：「无人机做高温测试时环境应该如何设置」「坦克进行振动试验时的环境分类和约束是什么」
31. 该业务须 LLM 推理合成：先查装备→项目→环境，再提取环境属性（Type/Class/Combination/Limit/Request），最后结合装备和项目给出具体设置建议
"""
