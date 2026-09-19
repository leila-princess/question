import { useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import styles from '../../styles/UserForm.module.css';

export default function AddUser() {
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    password: '',
    role: '',
    department: '',
    status: 'active'
  });

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    // 这里添加保存用户的逻辑
    console.log('用户数据:', formData);
    alert('用户添加成功');
  };

  return (
    <div className={styles.container}>
      <Head>
        <title>添加用户 - 环境试验标准规范管理系统</title>
      </Head>

      <div className={styles.header}>
        <h2>添加用户</h2>
        <Link href="/users" passHref>
          <button className={styles.backButton}>
            <i className="bi bi-arrow-left"></i> 返回用户列表
          </button>
        </Link>
      </div>

      <div className={styles.card}>
        <form onSubmit={handleSubmit}>
          <div className={styles.formGroup}>
            <label>用户名</label>
            <input
              type="text"
              name="name"
              value={formData.name}
              onChange={handleChange}
              required
            />
          </div>
          <div className={styles.formGroup}>
            <label>邮箱</label>
            <input
              type="email"
              name="email"
              value={formData.email}
              onChange={handleChange}
              required
            />
          </div>
          <div className={styles.formGroup}>
            <label>密码</label>
            <input
              type="password"
              name="password"
              value={formData.password}
              onChange={handleChange}
              required
            />
          </div>
          <div className={styles.formGroup}>
            <label>角色</label>
            <select
              name="role"
              value={formData.role}
              onChange={handleChange}
              required
            >
              <option value="">请选择角色</option>
              <option value="管理员">管理员</option>
              <option value="管理人员">管理人员</option>
              <option value="试验人员">试验人员</option>
              <option value="只读用户">只读用户</option>
            </select>
          </div>
          <div className={styles.formGroup}>
            <label>部门</label>
            <select
              name="department"
              value={formData.department}
              onChange={handleChange}
              required
            >
              <option value="">请选择部门</option>
              <option value="试验部">试验部</option>
              <option value="管理部">管理部</option>
              <option value="技术部">技术部</option>
              <option value="质量部">质量部</option>
            </select>
          </div>
          <div className={styles.formGroup}>
            <label>状态</label>
            <select
              name="status"
              value={formData.status}
              onChange={handleChange}
            >
              <option value="active">活跃</option>
              <option value="inactive">禁用</option>
            </select>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.saveButton}>
              保存用户
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}