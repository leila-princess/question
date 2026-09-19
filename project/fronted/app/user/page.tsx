"use client";
export const dynamic = 'force-dynamic';
import { useState, useEffect } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import styles from '../styles/Users.module.css';
import GlobalNav from '@/components/GlobalNav';
import { Drawer, List, Spin, Empty } from 'antd';
import { MenuOutlined } from '@ant-design/icons';
import router from 'next/router';

interface User {
  id: number;
  user_id: string;
  name: string;
  role: string;
  permissions: Record<string, any>;
  status: string;
  created_at: string;
}

export default function UsersPage() {
  const [activeTab, setActiveTab] = useState('userList');
  const [searchTerm, setSearchTerm] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/xtgl/users')
      .then(res => res.json())
      .then(result => {
        if (result.success) {
          setUsers(result.data || []);
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const stats = {
    totalUsers: users.length,
    activeUsers: users.filter(u => u.status === 'active').length,
    admins: users.filter(u => u.role === 'admin').length,
    newThisMonth: 0
  };

  const filteredUsers = users.filter(user => {
    const matchesSearch = user.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
                         user.user_id.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesRole = roleFilter === 'all' || user.role === roleFilter;
    const matchesStatus = statusFilter === 'all' || user.status === statusFilter;
    return matchesSearch && matchesRole && matchesStatus;
  });

  const deleteUser = (id: number) => {
    fetch(`/api/xtgl/users/${id}`, { method: 'DELETE' })
      .then(res => res.json())
      .then(result => {
        if (result.success) {
          setUsers(users.filter(user => user.id !== id));
        }
      });
  };

  return (
         <div style={{
            display: "flex",
            minHeight: "100vh",
            background: "linear-gradient(135deg, #f8fafc 0%, #e8f0fe 100%)"
          }}>
            <div style={{
              width: drawerOpen ? 260 : 0,
              transition: "width 0.3s",
              overflow: "hidden",
              flexShrink: 0
            }}>
        <Drawer
                placement="left"
                closable={false}
                onClose={() => setDrawerOpen(false)}
                open={drawerOpen}
                size={260}
                styles={{ body: { padding: 0, display: "flex", flexDirection: "column", height: "100%" } }}
                mask={false}
            >
                <div style={{ padding: "18px 20px 10px 20px", borderBottom: "1px solid #f0f0f0", display: "flex", alignItems: "center" }}>
                <MenuOutlined style={{ fontSize: 20, marginRight: 8, cursor: "pointer" }} onClick={() => setDrawerOpen(false)} />
                <span style={{ fontWeight: 700, color: "#235cff", fontSize: 18 }}>用户管理</span>
                </div>
                
                
                 <div style={{ 
                  borderTop: "1px solid rgba(59, 130, 246, 0.2)", 
                  padding: "24px", 
                  background: "rgba(219, 234, 254, 0.5)" 
                }}>
                  <GlobalNav />
                </div>
            </Drawer>
            </div>
            {!drawerOpen && (
              <div 
                style={{ 
                  width: 36, 
                  height: 36, 
                  background: "#fff", 
                  borderRadius: "0 12px 12px 0", 
                  boxShadow: "0 2px 8px 0 rgba(60,72,88,0.10)", 
                  display: "flex", 
                  alignItems: "center", 
                  justifyContent: "center", 
                  margin: "24px 0 0 0", 
                  cursor: "pointer", 
                  zIndex: 10 
                }} 
                onClick={() => setDrawerOpen(true)}
                aria-label="打开侧边栏"
              >
                <MenuOutlined style={{ fontSize: 20, color: "#235cff" }} />
              </div>
            )}
        <div style={{
          flex: 1,
          transition: "margin-left 0.3s",
          marginLeft: drawerOpen ? 0 : 0,
          padding: 24,
          maxWidth: "100vw"
        }}>
          
      <div className={styles.container}>
        <Head>
          <title>用户管理 - 环境试验标准规范管理系统</title>
        </Head>

        <div className={styles.header}>
          <h2>用户管理</h2>
        </div>

        <div className={styles.tabs}>
          <button 
            className={`${styles.tab} ${activeTab === 'userList' ? styles.active : ''}`}
            onClick={() => setActiveTab('userList')}
            aria-label="用户列表"
          >
            用户列表
          </button>
          <button 
            className={`${styles.tab} ${activeTab === 'roles' ? styles.active : ''}`}
            onClick={() => setActiveTab('roles')}
            aria-label="角色权限"
          >
            角色权限
          </button>
        </div>

        <div className={styles.content}>
          {activeTab === 'userList' && (
            <>
              <div className={styles.statsRow}>
                <div className={styles.statCard}>
                  <div className={styles.statNumber}>{stats.totalUsers}</div>
                  <div className={styles.statLabel}>总用户数</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statNumber}>{stats.activeUsers}</div>
                  <div className={styles.statLabel}>活跃用户</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statNumber}>{stats.admins}</div>
                  <div className={styles.statLabel}>管理员</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statNumber}>{stats.newThisMonth}</div>
                  <div className={styles.statLabel}>本月新增</div>
                </div>
              </div>

              <div className={styles.card}>
                <div className={styles.cardHeader}>
                  <h3>用户列表</h3>
                  <Link href="/users/add" passHref>
                    <button className={styles.primaryButton} aria-label="添加用户">
                      <i className="bi bi-plus"></i> 添加用户
                    </button>
                  </Link>
                </div>

                <div className={styles.filterBar}>
                  <div className={styles.searchBox}>
                    <i className="bi bi-search"></i>
                    <input 
                      type="text" 
                      placeholder="搜索用户..." 
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                      aria-label="搜索用户"
                    />
                  </div>
                  <div className={styles.filters}>
                    <select 
                      className={styles.filterSelect}
                      value={roleFilter}
                      onChange={(e) => setRoleFilter(e.target.value)}
                      aria-label="角色筛选"
                    >
                      <option value="all">所有角色</option>
                      <option value="管理员">管理员</option>
                      <option value="试验人员">试验人员</option>
                      <option value="管理人员">管理人员</option>
                      <option value="只读用户">只读用户</option>
                    </select>
                    <select 
                      className={styles.filterSelect}
                      value={statusFilter}
                      onChange={(e) => setStatusFilter(e.target.value)}
                      aria-label="状态筛选"
                    >
                      <option value="all">所有状态</option>
                      <option value="active">活跃</option>
                      <option value="inactive">禁用</option>
                    </select>
                  </div>
                  <button className={styles.secondaryButton} aria-label="导出数据">
                    <i className="bi bi-download"></i> 导出
                  </button>
                </div>

                <div className={styles.tableContainer}>
                  {loading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div> :
                   filteredUsers.length === 0 ? <Empty description="暂无用户数据" /> :
                  <table className={styles.userTable}>
                    <thead>
                      <tr>
                        <th>用户</th>
                        <th>角色</th>
                        <th>状态</th>
                        <th>创建时间</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredUsers.map(user => (
                        <tr key={user.id}>
                          <td>
                            <div className={styles.userCell}>
                              <div className={styles.userAvatar} style={{ backgroundColor: '#3498db' }}>
                                {user.name.charAt(0)}
                              </div>
                              <div>
                                <div className={styles.userName}>{user.name}</div>
                                <div className={styles.userEmail}>{user.user_id}</div>
                              </div>
                            </div>
                          </td>
                          <td>
                            <span className={`${styles.roleBadge} ${
                              user.role === 'admin' ? styles.adminBadge :
                              user.role === 'manager' ? styles.managerBadge :
                              user.role === 'user' ? styles.userBadge : styles.viewerBadge
                            }`}>
                              {user.role}
                            </span>
                          </td>
                          <td>
                            <span className={`${styles.statusBadge} ${
                              user.status === 'active' ? styles.activeBadge : styles.inactiveBadge
                            }`}>
                              {user.status === 'active' ? '活跃' : '禁用'}
                            </span>
                          </td>
                          <td>{user.created_at ? new Date(user.created_at).toLocaleString('zh-CN') : '-'}</td>
                          <td>
                            <div className={styles.actionButtons}>
                              <button 
                                className={styles.deleteButton}
                                onClick={() => deleteUser(user.id)}
                                aria-label={`删除用户 ${user.name}`}
                              >
                                <i className="bi bi-trash"></i>
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  }
                </div>

                <div className={styles.pagination}>
                  <button className={styles.pageButton} disabled aria-label="上一页">
                    <i className="bi bi-chevron-left"></i>
                  </button>
                  <button className={`${styles.pageButton} ${styles.active}`} aria-label="第1页">1</button>
                  <button className={styles.pageButton} aria-label="第2页">2</button>
                  <button className={styles.pageButton} aria-label="第3页">3</button>
                  <button className={styles.pageButton} aria-label="下一页">
                    <i className="bi bi-chevron-right"></i>
                  </button>
                </div>
              </div>
            </>
          )}

          {activeTab === 'roles' && (
            <div className={styles.card}>
              <div className={styles.cardHeader}>
                <h3>角色权限</h3>
                <button className={styles.primaryButton} aria-label="新建角色">
                  <i className="bi bi-plus"></i> 新建角色
                </button>
              </div>

              <div className={styles.roleGrid}>
                <div className={styles.roleCard}>
                  <div className={styles.roleHeader}>
                    <div>
                      <div className={`${styles.roleIcon} ${styles.adminIcon}`}>
                        <i className="bi bi-shield-lock"></i>
                      </div>
                      <h4>管理员</h4>
                      <small>2 个用户</small>
                    </div>
                    <button className={styles.settingsButton} aria-label="管理员设置">
                      <i className="bi bi-gear"></i>
                    </button>
                  </div>
                  <ul className={styles.permissionList}>
                    <li>系统配置管理</li>
                    <li>用户管理</li>
                    <li>角色权限管理</li>
                    <li>数据备份恢复</li>
                    <li>所有功能权限</li>
                  </ul>
                </div>

                <div className={styles.roleCard}>
                  <div className={styles.roleHeader}>
                    <div>
                      <div className={`${styles.roleIcon} ${styles.managerIcon}`}>
                        <i className="bi bi-person-badge"></i>
                      </div>
                      <h4>管理人员</h4>
                      <small>8 个用户</small>
                    </div>
                    <button className={styles.settingsButton} aria-label="管理人员设置">
                      <i className="bi bi-gear"></i>
                    </button>
                  </div>
                  <ul className={styles.permissionList}>
                    <li>试验方案审批</li>
                    <li>数据统计分析</li>
                    <li>报告生成导出</li>
                    <li>部门管理</li>
                    <li>用户查看</li>
                  </ul>
                </div>

                <div className={styles.roleCard}>
                  <div className={styles.roleHeader}>
                    <div>
                      <div className={`${styles.roleIcon} ${styles.userIcon}`}>
                        <i className="bi bi-person-workspace"></i>
                      </div>
                      <h4>试验人员</h4>
                      <small>25 个用户</small>
                    </div>
                    <button className={styles.settingsButton} aria-label="试验人员设置">
                      <i className="bi bi-gear"></i>
                    </button>
                  </div>
                  <ul className={styles.permissionList}>
                    <li>标准规范查询</li>
                    <li>试验方案设计</li>
                    <li>数据录入</li>
                    <li>报告生成</li>
                    <li>个人数据管理</li>
                  </ul>
                </div>

                <div className={styles.roleCard}>
                  <div className={styles.roleHeader}>
                    <div>
                      <div className={`${styles.roleIcon} ${styles.viewerIcon}`}>
                        <i className="bi bi-eye"></i>
                      </div>
                      <h4>只读用户</h4>
                      <small>10 个用户</small>
                    </div>
                    <button className={styles.settingsButton} aria-label="只读用户设置">
                      <i className="bi bi-gear"></i>
                    </button>
                  </div>
                  <ul className={styles.permissionList}>
                    <li>标准规范查看</li>
                    <li>试验数据查看</li>
                    <li>报告查看</li>
                  </ul>
                </div>
              </div>

              <div className={styles.permissionMatrix}>
                <h4>权限矩阵</h4>
                <div className={styles.matrixContainer}>
                  <table className={styles.matrixTable}>
                    <thead>
                      <tr>
                        <th>功能模块</th>
                        <th>管理员</th>
                        <th>管理人员</th>
                        <th>试验人员</th>
                        <th>只读用户</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>关联查询</td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                      </tr>
                      <tr>
                        <td>智能问答</td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                      </tr>
                      <tr>
                        <td>方案设计</td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                      </tr>
                      <tr>
                        <td>标准阅读</td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                      </tr>
                      <tr>
                        <td>知识管理</td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                      </tr>
                      <tr>
                        <td>用户管理</td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                      </tr>
                      <tr>
                        <td>系统设置</td>
                        <td><i className="bi bi-check-circle-fill granted" aria-label="已授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                        <td><i className="bi bi-x-circle-fill denied" aria-label="未授权"></i></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
    </div>
  );
}