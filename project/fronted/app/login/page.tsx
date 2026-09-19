"use client";
export const dynamic = 'force-dynamic';

import { useState, useEffect } from "react";
import { Button, Input, Tabs, Form, message, Typography, Space, App, Checkbox } from "antd";
import { UserOutlined, LockOutlined, MobileOutlined, SafetyOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { resolveDisplayName } from "@/utils/userDisplay";
import { normalizeUserRole } from "@/utils/permissions";

const { Title, Text } = Typography;

export default function LoginPage() {
  const [tab, setTab] = useState("account");
  const [loading, setLoading] = useState(false);
  const [rememberPassword, setRememberPassword] = useState(false);
  const router = useRouter();
  const [form] = Form.useForm();

  useEffect(() => {
    const savedUsername = localStorage.getItem("rememberedUsername");
    const savedPassword = localStorage.getItem("rememberedPassword");
    if (savedUsername && savedPassword) {
      form.setFieldsValue({
        username: savedUsername,
        password: savedPassword,
      });
      setRememberPassword(true);
    }
  }, [form]);

  const onFinish = async (values: any) => {
    setLoading(true);
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: values.username,
          password: values.password,
          remember: rememberPassword,
        }),
      });

      const result = await res.json();

      if (result.success) {
        message.success("登录成功");
        const displayName = resolveDisplayName(
          result.data.userName,
          result.data.userId || values.username,
          "用户",
        );
        localStorage.setItem("isLogin", "true");
        localStorage.setItem("userId", result.data.userId);
        localStorage.setItem("userName", displayName);
        localStorage.setItem("userRole", normalizeUserRole(result.data.role || "试验人员"));

        if (rememberPassword) {
          localStorage.setItem("rememberedUsername", values.username);
          localStorage.setItem("rememberedPassword", values.password);
        } else {
          localStorage.removeItem("rememberedUsername");
          localStorage.removeItem("rememberedPassword");
        }

        if (result.data.token) {
          localStorage.setItem("token", result.data.token);
        }

        router.push("/");
      } else {
        message.error(result.error || "登录失败");
      }
    } catch (error) {
      message.error("登录请求失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <App>
      <div
        style={{
          minHeight: "100vh",
          background: "linear-gradient(135deg, #f8fafc 0%, #e8f0fe 100%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            width: 900,
            maxWidth: "98vw",
            background: "#fff",
            borderRadius: 18,
            boxShadow: "0 4px 32px 0 rgba(60, 72, 88, 0.10)",
            display: "flex",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              flex: 1,
              background: "linear-gradient(135deg, #e8f0fe 0%, #f8fafc 100%)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: "48px 24px",
            }}
          >
            <Title level={2} style={{ color: "#235cff", marginBottom: 16 }}>
              XX系统原型
            </Title>
            <Text type="secondary" style={{ fontSize: 16 }}>
              智能问答 · 精准搜索 · 数据管理 · 用户管理
            </Text>
          </div>
          <div
            style={{
              width: 380,
              padding: "48px 32px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              borderLeft: "1px solid #f0f0f0",
            }}
          >
            <Title level={4} style={{ textAlign: "center", marginBottom: 32 }}>
              用户登录
            </Title>
            <Tabs
              activeKey={tab}
              onChange={setTab}
              centered
              items={[
                { key: "account", label: "账号密码登录" },
                { key: "mobile", label: "" },
              ]}
              style={{ marginBottom: 24 }}
            />
            <Form
              form={form}
              layout="vertical"
              onFinish={onFinish}
              autoComplete="off"
              requiredMark={false}
            >
              {tab === "account" ? (
                <>
                  <Form.Item
                    name="username"
                    rules={[{ required: true, message: "请输入用户名" }]}
                  >
                    <Input
                      prefix={<UserOutlined />}
                      placeholder="用户名"
                      size="large"
                      autoFocus
                    />
                  </Form.Item>
                  <Form.Item
                    name="password"
                    rules={[{ required: true, message: "请输入密码" }]}
                  >
                    <Input.Password
                      prefix={<LockOutlined />}
                      placeholder="密码"
                      size="large"
                    />
                  </Form.Item>
                  <Form.Item>
                    <Checkbox
                      checked={rememberPassword}
                      onChange={(e) => setRememberPassword(e.target.checked)}
                    >
                      记住密码
                    </Checkbox>
                  </Form.Item>
                </>
              ) : (
                <>
                  <Form.Item
                    name="phone"
                    rules={[
                      { required: true, message: "请输入手机号" },
                      { pattern: /^1\d{10}$/, message: "请输入有效手机号" },
                    ]}
                  >
                    <Input
                      prefix={<MobileOutlined />}
                      placeholder="手机号"
                      size="large"
                    />
                  </Form.Item>
                  <Form.Item
                    name="code"
                    rules={[{ required: true, message: "请输入验证码" }]}
                  >
                    <Input
                      prefix={<SafetyOutlined />}
                      placeholder="验证码"
                      size="large"
                    />
                  </Form.Item>
                </>
              )}
              <Form.Item>
                <Space style={{ width: "100%", justifyContent: "center" }}>
                  <Button
                    type="primary"
                    htmlType="submit"
                    size="large"
                    loading={loading}
                    style={{
                      borderRadius: 8,
                      minWidth: 120,
                      fontWeight: 600,
                      background: "linear-gradient(90deg, #4f8cff 0%, #235cff 100%)",
                      border: "none",
                    }}
                  >
                    登录
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </div>
        </div>
      </div>
    </App>
  );
}
