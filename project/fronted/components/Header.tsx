'use client';

import { 
  BellOutlined, 
  UserOutlined,
  LogoutOutlined,
  SearchOutlined as SearchIcon,
  MessageOutlined,
  ExperimentOutlined,
  BookOutlined,
  DatabaseOutlined,
  SettingOutlined,
  DashboardOutlined,
  TeamOutlined,
  HistoryOutlined,
  AimOutlined,
  ApartmentOutlined,
  ToolOutlined,
  RobotOutlined,
  ScanOutlined,
  PieChartOutlined,
  CloudServerOutlined,
  TagsOutlined,
  FileOutlined,
  CloudUploadOutlined,
  QuestionCircleOutlined,
  FileTextOutlined,
  DownOutlined,
  ReadOutlined,
} from "@ant-design/icons";
import { 
  Avatar, 
  Input,
  Badge,
  Space,
  message,
  Menu,
  Modal,
  Spin,
  Button,
  Tooltip,
} from "antd";
import { useRouter, usePathname } from "next/navigation";
import type { MenuProps } from 'antd';
import { useState, useEffect, useMemo } from 'react';
import { getPageInfoByPath } from '@/utils/layoutConfig';
import { filterMenuByRole, canAccessPage, normalizeUserRole, type MenuItemLike } from '@/utils/permissions';
import { resolveDisplayName } from '@/utils/userDisplay';
import SystemHelpModal from '@/components/SystemHelpModal';

type MenuItem = Required<MenuProps>['items'][number];

/** 各菜单项悬停提示（一句话精华说明） */
const MENU_TIPS: Record<string, string> = {
  home: '系统概览与常用功能快捷入口',
  GLCX: '图谱、全文、问答等多方式检索试验关联数据',
  JQCX: '按装备、科目等条件在图谱中精确查数据',
  SSYQ: '关键词检索设备与指标，表格与知识图谱联动展示',
  ZNWD: '自然语言提问，图库与向量库联合检索作答',
  FZSJ: '辅助生成与优化试验方案',
  JYGZ: '依据预置规则自动组装试验方案',
  JYLLM: '大模型理解需求并智能生成试验方案',
  FLGL: '维护试验标准与项目分类体系',
  WJGL: '上传分类管理标准文档，支持向量库同步',
  ZSTPZS: '可视化浏览知识图谱节点与关联关系',
  KSHZS: '试验数据统计图表与多维可视化分析',
  XTGL: '系统全局参数、模型与数据库配置',
  YHGL: '创建用户并分配角色与页面访问权限',
  SJKGL: '配置图库、向量库与 MySQL 等数据库连接',
  MXGL: '管理 LLM、Embedding 等 AI 模型配置',
  SJBF: '定期备份与恢复系统核心数据',
  RZGL: '查看用户操作与系统访问审计日志',
};

function MenuLabelWithTip({
  text,
  tipKey,
  subMenu,
}: {
  text: string;
  tipKey: string;
  subMenu?: boolean;
}) {
  const tip = MENU_TIPS[tipKey];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {text}
      {subMenu && <DownOutlined style={{ fontSize: 10, marginLeft: 2 }} />}
      {tip && (
        <Tooltip title={tip} mouseEnterDelay={0.3}>
          <QuestionCircleOutlined
            style={{ fontSize: 12, color: 'rgba(0,0,0,0.35)', cursor: 'help' }}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          />
        </Tooltip>
      )}
    </span>
  );
}

/** 系统名称：主标题大字显示，副标题换行以节省横向宽度 */
const SYSTEM_TITLE_MAIN = 'XXXXXXXX';
const SYSTEM_TITLE_SUB = '试验原型系统';

function getItem(
  label: React.ReactNode,
  key: React.Key,
  icon?: React.ReactNode,
  children?: MenuItem[],
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
  } as MenuItem;
}

export default function Header() {
  const router = useRouter();
  const pathname = usePathname();
  const [userName, setUserName] = useState('用户');
  const [loginId, setLoginId] = useState('');
  const [userId, setUserId] = useState<number | null>(null);
  const [userRole, setUserRole] = useState('');
  
  // 弹窗相关状态
  const [modalVisible, setModalVisible] = useState(false);
  const [editingName, setEditingName] = useState('');
  const [editingRole, setEditingRole] = useState('');
  const [editingDepartment, setEditingDepartment] = useState('');
  const [editingPassword, setEditingPassword] = useState('');
  const [modalLoading, setModalLoading] = useState(false);
  const [fetchLoading, setFetchLoading] = useState(false);
  const [helpVisible, setHelpVisible] = useState(false);

  // 菜单配置（全量，按角色过滤后展示）
  const allMenuItems: MenuItem[] = [
    getItem(
      <MenuLabelWithTip text="关联查询" tipKey="GLCX" subMenu />,
      'GLCX',
      <SearchIcon />,
      [
        getItem(<MenuLabelWithTip text="精确查询" tipKey="JQCX" />, 'JQCX', <AimOutlined />),
        getItem(<MenuLabelWithTip text="图谱查询" tipKey="SSYQ" />, 'SSYQ', <SearchIcon />),
        getItem(<MenuLabelWithTip text="智能问答" tipKey="ZNWD" />, 'ZNWD', <MessageOutlined />),
      ],
    ),
    getItem(
      <MenuLabelWithTip text="辅助设计" tipKey="FZSJ" subMenu />,
      'FZSJ',
      <ExperimentOutlined />,
      [
        getItem(<MenuLabelWithTip text="基于规则的辅助设计" tipKey="JYGZ" />, 'JYGZ', <ToolOutlined />),
        getItem(<MenuLabelWithTip text="基于大模型的辅助设计" tipKey="JYLLM" />, 'JYLLM', <RobotOutlined />),
      ],
    ),
    getItem(<MenuLabelWithTip text="分类管理" tipKey="FLGL" />, 'FLGL', <BookOutlined />),
    // getItem(
    // <span>知识管理
    //   <DownOutlined style={{ fontSize: '10px', marginLeft: '6px' }} />
    // </span>, 
    // 'ZSGL', <DatabaseOutlined />, 
    // [
    //   getItem('文件管理', 'WJGL', <FileOutlined />),
    //   getItem('图谱展示', 'ZSTPZS', <PieChartOutlined />),
    // ]),
    // getItem('可视化展示', 'KSHZS', <DashboardOutlined />),
    getItem(<MenuLabelWithTip text="知识管理" tipKey="WJGL" />, 'WJGL', <FileOutlined />),
    getItem(<MenuLabelWithTip text="图谱展示" tipKey="ZSTPZS" />, 'ZSTPZS', <PieChartOutlined />),
    getItem(<MenuLabelWithTip text="可视化展示" tipKey="KSHZS" />, 'KSHZS', <DashboardOutlined />),
    getItem(
      <MenuLabelWithTip text="系统设置" tipKey="XTGL" subMenu />,
      'XTGL',
      <SettingOutlined />,
      [
        getItem(<MenuLabelWithTip text="用户管理" tipKey="YHGL" />, 'YHGL', <TeamOutlined />),
        getItem(<MenuLabelWithTip text="数据库管理" tipKey="SJKGL" />, 'SJKGL', <DatabaseOutlined />),
        getItem(<MenuLabelWithTip text="模型管理" tipKey="MXGL" />, 'MXGL', <RobotOutlined />),
        getItem(<MenuLabelWithTip text="数据备份" tipKey="SJBF" />, 'SJBF', <CloudUploadOutlined />),
        // getItem(<MenuLabelWithTip text="问题反馈" tipKey="WTGL" />, 'WTGL', <QuestionCircleOutlined />),
        getItem(<MenuLabelWithTip text="日志管理" tipKey="RZGL" />, 'RZGL', <FileTextOutlined />),
      ],
    ),
  ];

  const menuItems = useMemo(
    () => filterMenuByRole(
      allMenuItems as MenuItemLike[],
      userRole || (typeof window !== 'undefined' ? localStorage.getItem('userRole') : null) || '试验人员',
    ) as MenuItem[],
    [allMenuItems, userRole],
  );

  // 获取用户基本信息（优先从后端刷新）
  useEffect(() => {
    const storedUserId = localStorage.getItem('userId') || '';
    const storedUserName = localStorage.getItem('userName');
    const storedUserRole = localStorage.getItem('userRole');
    setLoginId(storedUserId);
    setUserName(resolveDisplayName(storedUserName, storedUserId, '用户'));
    if (storedUserRole) setUserRole(storedUserRole);

    const loadUserProfile = async () => {
      const currentUserId = localStorage.getItem('userId');
      if (!currentUserId || localStorage.getItem('isLogin') !== 'true') return;
      try {
        const token = localStorage.getItem('token');
        const response = await fetch(`/api/xtgl/yhgl/current-user?user_id=${encodeURIComponent(currentUserId)}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!response.ok) return;
        const result = await response.json();
        if (result.success && result.data) {
          const name = resolveDisplayName(
            result.data.name,
            result.data.user_id || currentUserId,
            '用户',
          );
          const role = normalizeUserRole(result.data.role || '试验人员');
          localStorage.setItem('userName', name);
          localStorage.setItem('userRole', role);
          setLoginId(result.data.user_id || currentUserId);
          setUserName(name);
          setUserRole(role);
        }
      } catch (error) {
        console.warn('刷新用户信息失败', error);
      }
    };

    loadUserProfile();
  }, []);

  // 无权限访问当前页面时跳转首页
  useEffect(() => {
    if (pathname === '/login') return;
    const role = userRole || localStorage.getItem('userRole') || '试验人员';
    if (pathname.startsWith('/Standards')) {
      if (!canAccessPage(role, 'WJGL')) {
        message.warning('无权访问该页面');
        router.replace('/');
      }
      return;
    }
    const page = getPageInfoByPath(pathname);
    if (page && !canAccessPage(role, page.key)) {
      message.warning('无权访问该页面');
      router.replace('/');
    }
  }, [pathname, userRole, router]);

  // 菜单点击处理
  const handleMenuClick: MenuProps['onClick'] = (e) => {
    const keyToPath: Record<string, string> = {
      'GLCX': '/GLCX',
      'JQCX': '/GLCX/JQCX',
      'SSYQ': '/GLCX/SSYQ',
      'ZNWD': '/GLCX/ZNWD',
      'TPCX': '/GLCX/TPCX',
      'FLGL': '/FLGL',
      'FZSJ': '/FZSJ',
      'JYGZ': '/FZSJ/JYGZ',
      'JYLLM': '/FZSJ/JYLLM',
      'WJGL': '/ZSGL/WJGL',
      'WJGL1': '/ZSGL/WJGL1',
      'ZSTPZS': '/ZSGL/ZSTPZS',
      'BQGL': '/ZSGL/BQGL',
      'KSHZS': '/KSHZS',
      'YHGL': '/XTGL/YHGL',
      'MXGL': '/XTGL/MXGL',
      'RZGL': '/XTGL/RZGL',
      'SJBF': '/XTGL/SJBF',
      'SJKGL': '/XTGL/SJKGL',
      // 'WTGL': '/XTGL/WTGL',
    };
    const path = keyToPath[e.key];
    if (path) router.push(path);
  };

  // 获取当前选中的菜单项
  const getSelectedKey = () => {
    // 标准/文件阅读页不在菜单路径表里，归入「知识管理」避免误高亮首页
    if (pathname === '/Standards' || pathname.startsWith('/Standards/')) {
      return 'WJGL';
    }
    const pathToKey: Record<string, string> = {
      '/GLCX': 'GLCX',
      '/GLCX/JQCX': 'JQCX',
      '/GLCX/SSYQ': 'SSYQ',
      '/GLCX/ZNWD': 'ZNWD',
      '/GLCX/TPCX': 'TPCX',
      '/FLGL': 'FLGL',
      '/FZSJ': 'FZSJ',
      '/FZSJ/JYGZ': 'JYGZ',
      '/FZSJ/JYLLM': 'JYLLM',
      '/ZSGL/WJGL': 'WJGL',
      '/ZSGL/WJGL1': 'WJGL1',
      '/ZSGL/ZSTPZS': 'ZSTPZS',
      '/ZSGL/BQGL': 'BQGL',
      '/KSHZS': 'KSHZS',
      '/XTGL/YHGL': 'YHGL',
      '/XTGL/MXGL': 'MXGL',
      '/XTGL/RZGL': 'RZGL',
      '/XTGL/SJBF': 'SJBF',
      '/XTGL/SJKGL': 'SJKGL',
      // '/XTGL/WTGL': 'WTGL',
      '/login': 'login',
    };
    return pathToKey[pathname] || '';
  };

  const selectedMenuKey = getSelectedKey();

  // 记录浏览历史
  useEffect(() => {
    const currentPage = getPageInfoByPath(pathname);
    if (currentPage) {
      const storedHistory = localStorage.getItem('navigation_history');
      let historyList = storedHistory ? JSON.parse(storedHistory) : [];
      historyList = historyList.filter((item: any) => item.path !== pathname);
      historyList.unshift({
        key: currentPage.key,
        label: currentPage.menuLabel,
        path: pathname,
        timestamp: Date.now()
      });
      historyList = historyList.slice(0, 20);
      localStorage.setItem('navigation_history', JSON.stringify(historyList));
    }
  }, [pathname]);

  // 打开弹窗时加载最新用户信息（优先从后端获取，否则用localStorage）
  const openModal = async () => {
    setModalVisible(true);
    setFetchLoading(true);
    try {
      const currentUserId = localStorage.getItem('userId');
      if (currentUserId) {
        const token = localStorage.getItem('token');
        const response = await fetch(`/api/xtgl/yhgl/current-user?user_id=${currentUserId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (response.ok) {
          const result = await response.json();
          if (result.success && result.data) {
            const name = resolveDisplayName(
              result.data.name,
              result.data.user_id || currentUserId,
              '用户',
            );
            const role = normalizeUserRole(result.data.role || '试验人员');
            setEditingName(name);
            setEditingRole(role);
            setEditingDepartment(result.data.department || '');
            setEditingPassword('');
            localStorage.setItem('userName', name);
            localStorage.setItem('userRole', role);
            setUserName(name);
            setUserRole(role);
          } else {
            setEditingName(localStorage.getItem('userName') || '');
            setEditingRole(localStorage.getItem('userRole') || '');
            setEditingDepartment('');
            setEditingPassword('');
          }
        } else {
          setEditingName(localStorage.getItem('userName') || '');
          setEditingRole(localStorage.getItem('userRole') || '');
          setEditingDepartment('');
          setEditingPassword('');
        }
      } else {
        setEditingName(localStorage.getItem('userName') || '');
        setEditingRole(localStorage.getItem('userRole') || '');
        setEditingDepartment('');
        setEditingPassword('');
      }
    } catch (error) {
      console.warn('获取用户详情失败，使用本地数据', error);
      setEditingName(localStorage.getItem('userName') || '');
      setEditingRole(localStorage.getItem('userRole') || '');
      setEditingDepartment('');
      setEditingPassword('');
    } finally {
      setFetchLoading(false);
    }
  };

  // 保存修改
  const handleSave = async () => {
    const currentUserId = localStorage.getItem('userId');
    if (!currentUserId) {
      message.error('用户信息缺失，请重新登录');
      return;
    }

    setModalLoading(true);
    try {
      const token = localStorage.getItem('token');
      const payload: Record<string, string> = {
        user_id: currentUserId,
        name: editingName,
        department: editingDepartment,
      };
      if (editingPassword) {
        payload.password = editingPassword;
      }

      const response = await fetch('/api/xtgl/yhgl/current-user', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
      });

      const result = await response.json();
      if (result.success) {
        localStorage.setItem('userName', editingName);
        setUserName(editingName);
        message.success('保存成功');
        setModalVisible(false);
        setEditingPassword('');
      } else {
        message.error(result.error || '保存失败');
      }
    } catch (error) {
      console.error('保存失败:', error);
      message.error('网络错误，请稍后重试');
    } finally {
      setModalLoading(false);
    }
  };

  // 退出登录
  const handleLogout = async () => {
    setModalVisible(false);
    try {
      await fetch('/api/logout', { method: 'POST' });
    } catch (error) {
      // 忽略错误
    }
    localStorage.removeItem('isLogin');
    localStorage.removeItem('userId');
    localStorage.removeItem('userName');
    localStorage.removeItem('userRole');
    localStorage.removeItem('userPermissions');
    localStorage.removeItem('userStatus');
    localStorage.removeItem('token');
    message.success('已退出登录');
    router.push('/login');
  };

  if (pathname === '/login') {
    return null;
  }

  return (
    <div style={{ 
      background: '#fff',
      borderBottom: '1px solid #f0f0f0',
      padding: '0 24px',
      position: 'sticky',
      top: 0,
      zIndex: 100
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        height: 64,
        minWidth: 0,
      }}>
        {/* 系统名称：两行排版，显眼且少占横向空间 */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => router.push('/')}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              router.push('/');
            }
          }}
          style={{
            flexShrink: 0,
            maxWidth: 168,
            lineHeight: 1.15,
            cursor: 'pointer',
          }}
          title={`${SYSTEM_TITLE_MAIN}${SYSTEM_TITLE_SUB}（返回首页）`}
        >
          <div
            style={{
              fontSize: 22,
              fontWeight: 800,
              color: '#235cff',
              letterSpacing: 0.5,
            }}
          >
            {SYSTEM_TITLE_MAIN}
          </div>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: '#235cff',
              opacity: 0.88,
              marginTop: 2,
            }}
          >
            {SYSTEM_TITLE_SUB}
          </div>
        </div>

        {/* 水平菜单栏：可收缩，窄屏时 Ant Design 会自动收进「更多」 */}
        <div style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
          <Menu
            mode="horizontal"
            selectedKeys={selectedMenuKey ? [selectedMenuKey] : []}
            items={menuItems}
            onClick={handleMenuClick}
            style={{
              borderBottom: 'none',
              background: 'transparent',
              lineHeight: '64px',
              minWidth: 0,
            }}
            triggerSubMenuAction="hover"
            expandIcon={({ isOpen }) => <DownOutlined rotate={isOpen ? 180 : 0} />}
          />
        </div>

        {/* 帮助 + 用户区 */}
        <div
          style={{
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            marginLeft: 8,
          }}
        >
          <Tooltip title="帮助与更新说明">
            <Button
              type="text"
              icon={<ReadOutlined style={{ fontSize: 18 }} />}
              aria-label="帮助"
              onClick={() => setHelpVisible(true)}
              style={{ color: '#666' }}
            />
          </Tooltip>
          <div
            style={{
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              maxWidth: 160,
            }}
            onClick={openModal}
          >
            <Avatar icon={<UserOutlined />} style={{ backgroundColor: '#235cff', flexShrink: 0 }} />
            <span
              style={{
                fontSize: 14,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {resolveDisplayName(userName, loginId, '用户')}
            </span>
          </div>
        </div>
      </div>

      <SystemHelpModal open={helpVisible} onClose={() => setHelpVisible(false)} />

      {/* 个人信息编辑弹窗 */}
      <Modal
        title="个人信息"
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        footer={null}
        width={480}
        destroyOnHidden
      >
        {fetchLoading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin tip="加载中..." />
          </div>
        ) : (
          <div style={{ padding: '8px 0' }}>
            <div style={{ marginBottom: 20 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>登录账号</div>
              <Input value={loginId || ''} disabled />
            </div>
            <div style={{ marginBottom: 20 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>姓名</div>
              <Input value={editingName} disabled />
            </div>
            <div style={{ marginBottom: 20 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>角色</div>
              <Input value={editingRole} disabled />
            </div>
            <div style={{ marginBottom: 20 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>部门</div>
              <Input
                placeholder="请输入部门"
                value={editingDepartment}
                onChange={(e) => setEditingDepartment(e.target.value)}
              />
            </div>
            <div style={{ marginBottom: 28 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>密码</div>
              <Input.Password
                placeholder="输入新密码（留空不修改）"
                value={editingPassword}
                onChange={(e) => setEditingPassword(e.target.value)}
              />
            </div>
            <Button
              type="primary"
              block
              loading={modalLoading}
              onClick={handleSave}
              style={{ marginBottom: 12 }}
            >
              保存修改
            </Button>
            <Button
              danger
              block
              icon={<LogoutOutlined />}
              onClick={handleLogout}
            >
              退出登录
            </Button>
          </div>
        )}
      </Modal>
    </div>
  );
}