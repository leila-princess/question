'use client';

export const dynamic = 'force-dynamic';

import React, { useState, Suspense } from 'react';
import {
  Table, Tag, Space, Button, Popconfirm, Image, Typography, Modal, Input, message,
} from 'antd';
import { EditOutlined, DeleteOutlined, EyeOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

const { Text } = Typography;

// 节点类型定义
interface KGNode {
  id: string;
  labels: string[];
  properties: Record<string, any>;
}

// 表格数据结构
interface TableData {
  headers: string[];
  rows: (string | number)[][];
}

// ---------- 模拟数据（使用 useState 以便修改）----------
const initialNodes: KGNode[] = [
  {
    id: "std_GB/T38924.6-2020",
    labels: ["Standard"],
    properties: {
      number: "GB/T 38924.6-2020",
      name: "GB/T 38924.6-2020",
      definition: "民用轻小型无人机系统环境试验方法 第6部分：振动试验",
      context: "中华人民共和国国家标准",
      date: "2020-07-21"
    }
  },
  {
    id: "clause_GB/T38924.6-2020_4.1",
    labels: ["Clause"],
    properties: {
      number: "4.1",
      name: "试验条件",
      definition: "试验应在标准大气条件下进行，温度23℃±5℃，相对湿度45%～75%。"
    }
  },
  {
    id: "table_GB/T38924.6-2020_1",
    labels: ["Table"],
    properties: {
      number: "表1",
      name: "无人机系统振动试验等级分类",
      tableData: {
        headers: [
          "应用类别",
          "典型使用环境",
          "振动类型",
          "加速度均方根值（g rms）",
          "频率范围（Hz）",
          "持续时间（h/轴）"
        ],
        rows: [
          ["城市低空物流", "平稳气流、短时湍流", "随机", 8.5, "20–2000", 3],
          ["山区巡检", "中等湍流、阵风", "随机", 12, "10–2000", 4],
          ["应急救援", "强湍流、突风、着陆冲击耦合", "正弦+随机", "10 g（正弦峰值）+15 g rms（随机）", "5–2000", "2（正弦）+5（随机）"],
          ["农业植保", "低空紊流、药液晃动激励", "随机", 6, "5–1000", 2]
        ]
      }
    }
  },
  {
    id: "figure_GB/T38924.6-2020_2",
    labels: ["Figure"],
    properties: {
      number: "图2",
      name: "振动试验安装示意图",
      imagePath: "15_img2",
      definition: "无人机固定在振动台上，三个轴向依次进行试验。"
    }
  },
  {
    id: "person_高凤杰",
    labels: ["Person"],
    properties: {
      name: "高凤杰"
    }
  },
  {
    id: "organization_国家标准化管理委员会",
    labels: ["Organization"],
    properties: {
      name: "国家标准化管理委员会"
    }
  }
];

// ---------- 辅助函数 ----------
const getImageUrl = (imagePath: string) => {
  const match = imagePath.match(/(\d+)_img(\d+)/);
  if (match) {
    return `/figures/figure_p${match[1]}_${match[2]}.png`;
  }
  return '';
};

const handleEditNode = (node: KGNode) => {
  console.log('编辑节点:', node);
  message.info(`编辑节点：${node.id}（此功能仅为演示）`);
};

const handleDeleteNode = (id: string) => {
  console.log('删除节点:', id);
  message.warning(`删除节点：${id}（此功能仅为演示）`);
};

// ---------- 可编辑表格 Modal 组件 ----------
interface TableEditModalProps {
  visible: boolean;
  node: KGNode | null;
  onClose: () => void;
  onSave: (nodeId: string, newTableData: TableData) => void;
}

const TableEditModal: React.FC<TableEditModalProps> = ({ visible, node, onClose, onSave }) => {
  const [tableData, setTableData] = useState<TableData | null>(null);

  // 当 node 变化时，初始化 tableData
  React.useEffect(() => {
    if (node && node.properties.tableData) {
      setTableData(JSON.parse(JSON.stringify(node.properties.tableData))); // 深拷贝
    }
  }, [node]);

  const handleCellChange = (rowIdx: number, colIdx: number, value: string) => {
    if (!tableData) return;
    const newRows = [...tableData.rows];
    newRows[rowIdx][colIdx] = value;
    setTableData({ ...tableData, rows: newRows });
  };

  const handleSave = () => {
    if (node && tableData) {
      onSave(node.id, tableData);
      onClose();
    }
  };

  if (!node || !tableData) return null;

  return (
    <Modal
      title={`编辑表格 - ${node.properties.name || node.properties.number}`}
      open={visible}
      onCancel={onClose}
      onOk={handleSave}
      width={800}
      bodyStyle={{ maxHeight: '500px', overflow: 'auto' }}
    >
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {tableData.headers.map((header, idx) => (
              <th key={idx} style={{ border: '1px solid #ddd', padding: '8px', background: '#f5f5f5' }}>
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tableData.rows.map((row, rowIdx) => (
            <tr key={rowIdx}>
              {row.map((cell, colIdx) => (
                <td key={colIdx} style={{ border: '1px solid #ddd', padding: '4px' }}>
                  <Input
                    value={cell}
                    onChange={(e) => handleCellChange(rowIdx, colIdx, e.target.value)}
                    bordered={false}
                    style={{ width: '100%' }}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  );
};

// ---------- 主测试页面组件 ----------
const TestTable: React.FC = () => {
  const [nodes, setNodes] = useState<KGNode[]>(initialNodes);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingNode, setEditingNode] = useState<KGNode | null>(null);

  const handleOpenTableEdit = (node: KGNode) => {
    setEditingNode(node);
    setModalVisible(true);
  };

  const handleSaveTable = (nodeId: string, newTableData: TableData) => {
    const updatedNodes = nodes.map((n) => {
      if (n.id === nodeId) {
        return {
          ...n,
          properties: {
            ...n.properties,
            tableData: newTableData,
          },
        };
      }
      return n;
    });
    setNodes(updatedNodes);
    message.success('表格数据已更新');
  };

  // ---------- 列定义 ----------
  const nodeColumns: ColumnsType<KGNode> = [
    { title: '编号', dataIndex: ['properties', 'number'], key: 'number', width: 100 },
    { title: '命名', dataIndex: ['properties', 'name'], key: 'name', width: 200 },
    {
      title: '标签',
      dataIndex: 'labels',
      key: 'labels',
      width: 150,
      render: (labels: string[]) => labels.map((l) => <Tag key={l}>{l}</Tag>),
    },
    {
      title: '描述',
      dataIndex: ['properties', 'definition'],
      key: 'description',
      width: 300,
      render: (value: any) => {
        if (value) {
          return <Text>{String(value)}</Text>;
        }
        return <Text type="secondary">无描述</Text>;
      },
    },
    {
      title: '预览',
      key: 'preview',
      width: 200,
      render: (_, node) => {
        if (node.labels.includes('Figure') && node.properties.imagePath) {
          const imgUrl = getImageUrl(node.properties.imagePath);
          return (
            <Image
              src={imgUrl}
              alt="预览"
              style={{ maxWidth: '100px', maxHeight: '100px', objectFit: 'cover' }}
              fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."
            />
          );
        }
        if (node.labels.includes('Table') && node.properties.tableData) {
          return (
            <Button icon={<EyeOutlined />} onClick={() => handleOpenTableEdit(node)}>
              查看表格
            </Button>
          );
        }
        return null;
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, node) => (
        <Space>
          <Button icon={<EditOutlined />} size="small" onClick={() => handleEditNode(node)}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDeleteNode(node.id)}>
            <Button icon={<DeleteOutlined />} size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: '20px' }}>
      <h1>知识图谱节点表格测试</h1>
      <Table
        columns={nodeColumns}
        dataSource={nodes}
        rowKey="id"
        pagination={{ pageSize: 5 }}
        scroll={{ x: 'max-content' }}
      />

      <TableEditModal
        visible={modalVisible}
        node={editingNode}
        onClose={() => setModalVisible(false)}
        onSave={handleSaveTable}
      />
    </div>
  );
};

export default TestTable;