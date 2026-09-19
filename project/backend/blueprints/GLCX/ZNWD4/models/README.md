# MLP route classifier bundle

Place `router_mlp.pkl` here, or set `ZNWD4_MLP_BUNDLE_PATH` in `.env`.

## 换图谱后重训（推荐）

```bash
# 1) 从 Neo4j 自动采样节点 → 生成问句训练集
python EFHR/scripts/build_routebench_from_graph.py --with-robust --merge-telemetry

# 2) 训练 MLP 并部署到本目录
python EFHR/scripts/retrain_znwd4_mlp_router.py --from-graph --with-robust --merge-telemetry --deploy --tag graph_auto

# 重启 backend 后 ZNWD4 默认加载 router_mlp.pkl
```

## 仅重训已有 JSON

```bash
python EFHR/scripts/retrain_znwd4_mlp_router.py \
  --clean-source EFHR/data/routebench_from_graph_clean.json \
  --deploy --tag graph_auto
```

## 旧流程（固定 qa_pairs_500）

```bash
python EFHR/scripts/train_learned_routers.py --tag exp4_qwen25
```

Default fallback: `EFHR/experiments/case_a/exp4_qwen25/models/router_mlp_exp4_qwen25.pkl`
