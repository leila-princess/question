'use client';

import { 
  HomeOutlined, 
  SearchOutlined, 
  MessageOutlined, 
  ExperimentOutlined, 
  BookOutlined, 
  DatabaseOutlined, 
  UserOutlined, 
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
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  FileOutlined,
  CloudUploadOutlined,
  QuestionCircleOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { 
  Avatar, 
  Dropdown, 
  Divider,
  Button,
  Tooltip,
  Menu,
} from "antd";
import { useRouter, usePathname } from "next/navigation";
import type { MenuProps } from 'antd';
import { useState, useEffect } from 'react';
import { getPageInfoByPath } from '@/utils/layoutConfig';

type MenuItem = Required<MenuProps>['items'][number];

function getItem(
  label: React.ReactNode,
  key: React.Key,
  icon?: React.ReactNode,
  children?: MenuItem[],
  type?: 'group',
): MenuItem {
  return {
    key,
    icon,
    children,
    label,
    type,
  } as MenuItem;
}

interface HistoryItem {
  key: string;
  label: string;
  path: string;
}

interface GlobalNavProps {
  collapsed?: boolean;
  onCollapse?: (collapsed: boolean) => void;
}

export default function GlobalNav({ collapsed: propCollapsed, onCollapse }: GlobalNavProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const [currentPage, setCurrentPage] = useState<any>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  
  const collapsed = propCollapsed !== undefined ? propCollapsed : internalCollapsed;
  const setCollapsed = onCollapse || setInternalCollapsed;

  useEffect(() => {
    const page = getPageInfoByPath(pathname);
    setCurrentPage(page);

    const storedHistory = localStorage.getItem('navigation_history');
    if (storedHistory) {
      const historyList = JSON.parse(storedHistory);
      setHistory(historyList.slice(0, 5));
    }
  }, [pathname]);

  const getSelectedKey = () => {
    const pathToKey: Record<string, string> = {
      '/': 'home',
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
      '/ZSGL/ZSTQ': 'ZSTQ',
      '/ZSGL/ZSTPZS': 'ZSTPZS',
      // '/ZSGL/XLSJK': 'XLSJK',
      '/ZSGL/BQGL': 'BQGL',
      '/KSHZS': 'KSHZS',
      '/XTGL/YHGL': 'YHGL',
      '/XTGL/MXGL': 'MXGL',
      '/XTGL/RZGL': 'RZGL',
      '/XTGL/SJBF': 'SJBF',
      '/XTGL/SJKGL': 'SJKGL',
      '/XTGL/WTGL': 'WTGL',
      '/login': 'login',
    };
    return pathToKey[pathname] || 'home';
  };

  useEffect(() => {
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
      setHistory(historyList.slice(0, 5));
    }
  }, [currentPage, pathname]);

  const menuItems: MenuItem[] = [
    getItem('首页', 'home', <HomeOutlined />),
    getItem('关联查询', 'GLCX', <SearchOutlined />, [
      getItem('精确查询', 'JQCX', <AimOutlined />),
      getItem('图谱查询', 'SSYQ', <SearchOutlined />),
      // getItem('图谱查询', 'TPCX', <ApartmentOutlined />),
      getItem('智能问答', 'ZNWD', <MessageOutlined />),
    ]),
    getItem('辅助设计', 'FZSJ', <ExperimentOutlined />, [
      getItem('基于规则的辅助设计', 'JYGZ', <ToolOutlined />),
      getItem('基于大模型的辅助设计', 'JYLLM', <RobotOutlined />),
    ]),
    getItem('分类管理', 'FLGL', <BookOutlined />),
    getItem('知识管理', 'ZSGL', <DatabaseOutlined />, [
      getItem('文件管理', 'WJGL', <FileOutlined />),
      getItem('文件管理（增强版）', 'WJGL1', <ExperimentOutlined />),
      // getItem('知识提取', 'ZSTQ', <ScanOutlined />),
      getItem('图谱展示', 'ZSTPZS', <PieChartOutlined />),
      // getItem('向量数据库', 'XLSJK', <CloudServerOutlined />),
      // getItem('标签管理', 'BQGL', <TagsOutlined />),
    ]),
    getItem('可视化展示', 'KSHZS', <DashboardOutlined />),
    
    getItem('系统设置', 'YHGL', <SettingOutlined />,[
      getItem('用户管理', 'YHGL', <TeamOutlined />),
      getItem('数据库管理', 'SJKGL', <DatabaseOutlined />),
      getItem('模型管理', 'MXGL', <RobotOutlined />),
      getItem('数据备份', 'SJBF', <CloudUploadOutlined />),
      getItem('问题反馈', 'WTGL', <QuestionCircleOutlined />),
      getItem('日志管理', 'RZGL', <FileTextOutlined />),
    ]),
  ];
  const handleMenuClick: MenuProps['onClick'] = (e) => {
    const keyToPath: Record<string, string> = {
      'home': '/',
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
      'ZSTQ': '/ZSGL/ZSTQ',
      'ZSTPZS': '/ZSGL/ZSTPZS',
      // 'XLSJK': '/ZSGL/XLSJK',
      'BQGL': '/ZSGL/BQGL',
      'KSHZS': '/KSHZS',
      // 'XTGL': '/XTGL',
      'YHGL': '/XTGL/YHGL',
      'MXGL': '/XTGL/MXGL',
      'RZGL': '/XTGL/RZGL',
      'SJBF': '/XTGL/SJBF',
      'SJKGL': '/XTGL/SJKGL',
      'WTGL': '/XTGL/WTGL',
    };
    const path = keyToPath[e.key];
    if (path) router.push(path);
  };

  const userMenuItems: MenuProps['items'] = [
    { key: 'profile', label: '个人中心', icon: <UserOutlined />, onClick: () => router.push('/profile') },
    { key: 'account-settings', label: '账户设置', icon: <SettingOutlined />, onClick: () => router.push('/account-settings') },
    { type: 'divider' },
    { key: 'logout', label: '退出登录', danger: true, onClick: () => router.push('/login') },
  ];

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      backgroundColor: '#fff',
    }}>
      <div style={{
        padding: '12px 8px',
        borderBottom: '1px solid #f0f0f0',
        display: 'flex',
        justifyContent: 'center',
      }}>
        <Tooltip title={collapsed ? '展开菜单' : '收起菜单'}>
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            style={{ width: '100%' }}
          />
        </Tooltip>
      </div>

      <div style={{ 
        padding: collapsed ? '16px 8px' : '16px 16px',
        borderBottom: '1px solid #f0f0f0',
        backgroundColor: '#fafafa',
        minHeight: collapsed ? 'auto' : 72,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
      }}>
        {collapsed ? (
          <Tooltip title={currentPage?.menuLabel || '首页'} placement="right">
            <div style={{ fontSize: 20, color: '#235cff', textAlign: 'center' }}>
              {currentPage?.icon ? getIconComponent(currentPage.icon) : <HomeOutlined />}
            </div>
          </Tooltip>
        ) : (
          <>
            <div style={{ fontSize: 16, fontWeight: 'bold', color: '#235cff', marginBottom: 4 }}>
              {currentPage?.menuLabel || '首页'}
            </div>
            <div style={{ fontSize: 12, color: '#888', lineHeight: 1.4 }}>
              {currentPage?.description || '系统概览与快速入口'}
            </div>
          </>
        )}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        <Menu
          mode="inline"
          selectedKeys={[getSelectedKey()]}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ borderRight: 0 }}
          inlineCollapsed={collapsed}
        />
      </div>

      <div style={{ 
        padding: collapsed ? '12px 8px' : '12px 16px',
        borderTop: '1px solid #f0f0f0',
        backgroundColor: '#fafafa',
      }}>
        <Dropdown menu={{ items: userMenuItems }} placement="topRight" arrow>
          <div style={{
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            justifyContent: collapsed ? 'center' : 'flex-start',
          }}>
            <Avatar size="small" style={{ backgroundColor: '#235cff' }}>U</Avatar>
            {!collapsed && (
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: '#235cff', fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  管理员
                </div>
              </div>
            )}
          </div>
        </Dropdown>
      </div>
    </div>
  );
}

function getIconComponent(iconName?: string) {
  const iconMap: Record<string, React.ReactNode> = {
    'HomeOutlined': <HomeOutlined />,
    'SearchOutlined': <SearchOutlined />,
    'MessageOutlined': <MessageOutlined />,
    'ExperimentOutlined': <ExperimentOutlined />,
    'BookOutlined': <BookOutlined />,
    'DatabaseOutlined': <DatabaseOutlined />,
    'DashboardOutlined': <DashboardOutlined />,
    'TeamOutlined': <TeamOutlined />,
    'SettingOutlined': <SettingOutlined />,
    'HistoryOutlined': <HistoryOutlined />,
    'AimOutlined': <AimOutlined />,
    'ApartmentOutlined': <ApartmentOutlined />,
    'ToolOutlined': <ToolOutlined />,
    'RobotOutlined': <RobotOutlined />,
    'ScanOutlined': <ScanOutlined />,
    'PieChartOutlined': <PieChartOutlined />,
    'CloudServerOutlined': <CloudServerOutlined />,
    'TagsOutlined': <TagsOutlined />,
  };
  return iconName ? iconMap[iconName] : null;
}
