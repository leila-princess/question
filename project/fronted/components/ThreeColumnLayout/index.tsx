"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Button } from "antd";
import { LeftOutlined, RightOutlined } from "@ant-design/icons";

export interface ThreeColumnLayoutProps {
  leftPanel: React.ReactNode;
  middlePanel: React.ReactNode;
  rightPanel: React.ReactNode;
  layoutKey?: string;
  defaultLeftWidth?: number;
  defaultRightWidth?: number;
  minLeftWidth?: number;
  maxLeftWidth?: number;
  minRightWidth?: number;
  maxRightWidth?: number;
  collapsedWidth?: number;
  className?: string;
  style?: React.CSSProperties;
  header?: React.ReactNode;
}

const STORAGE_PREFIX = "three-column-layout-";

const ThreeColumnLayout: React.FC<ThreeColumnLayoutProps> = ({
  leftPanel,
  middlePanel,
  rightPanel,
  layoutKey = "default",
  defaultLeftWidth = 280,
  defaultRightWidth = 320,
  minLeftWidth = 180,
  maxLeftWidth = 500,
  minRightWidth = 200,
  maxRightWidth = 600,
  collapsedWidth = 32,
  className,
  style,
  header,
}) => {
  const [leftWidth, setLeftWidth] = useState(defaultLeftWidth);
  const [rightWidth, setRightWidth] = useState(defaultRightWidth);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [isDragging, setIsDragging] = useState<"left" | "right" | null>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(`${STORAGE_PREFIX}${layoutKey}`);
      if (saved) {
        const config = JSON.parse(saved);
        if (config.leftWidth) setLeftWidth(config.leftWidth);
        if (config.rightWidth) setRightWidth(config.rightWidth);
        if (typeof config.leftCollapsed === "boolean") setLeftCollapsed(config.leftCollapsed);
        if (typeof config.rightCollapsed === "boolean") setRightCollapsed(config.rightCollapsed);
      }
    } catch {
      // ignore
    }
  }, [layoutKey]);

  const saveLayout = useCallback(
    (lw: number, rw: number, lc: boolean, rc: boolean) => {
      try {
        localStorage.setItem(
          `${STORAGE_PREFIX}${layoutKey}`,
          JSON.stringify({ leftWidth: lw, rightWidth: rw, leftCollapsed: lc, rightCollapsed: rc })
        );
      } catch {
        // ignore
      }
    },
    [layoutKey]
  );

  useEffect(() => {
    saveLayout(leftWidth, rightWidth, leftCollapsed, rightCollapsed);
  }, [leftWidth, rightWidth, leftCollapsed, rightCollapsed, saveLayout]);

  useEffect(() => {
    if (!isDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      if (isDragging === "left") {
        setLeftWidth(Math.max(minLeftWidth, Math.min(maxLeftWidth, e.clientX - 32)));
      } else if (isDragging === "right") {
        setRightWidth(Math.max(minRightWidth, Math.min(maxRightWidth, window.innerWidth - 32 - e.clientX)));
      }
    };
    const handleMouseUp = () => setIsDragging(null);
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, minLeftWidth, maxLeftWidth, minRightWidth, maxRightWidth]);

  const toggleCollapse = (side: "left" | "right") => {
    if (side === "left") setLeftCollapsed(!leftCollapsed);
    else setRightCollapsed(!rightCollapsed);
  };

  const CollapseButton = ({ side }: { side: "left" | "right" }) => {
    const collapsed = side === "left" ? leftCollapsed : rightCollapsed;
    const isLeft = side === "left";

    if (collapsed) {
      return (
        <Button
          type="text"
          size="small"
          icon={isLeft ? <RightOutlined /> : <LeftOutlined />}
          onClick={() => toggleCollapse(side)}
          title="展开"
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            zIndex: 20,
            background: "#fff",
            border: "1px solid #e5e7eb",
            borderRadius: 4,
            width: 24,
            height: 24,
            padding: 0,
            boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        />
      );
    }

    return (
      <Button
        type="text"
        size="small"
        icon={isLeft ? <LeftOutlined /> : <RightOutlined />}
        onClick={() => toggleCollapse(side)}
        title="收起"
        style={{
          position: "absolute",
          top: "50%",
          [isLeft ? "right" : "left"]: 4,
          transform: "translateY(-50%)",
          zIndex: 20,
          background: "#fff",
          border: "1px solid #e5e7eb",
          borderRadius: 4,
          width: 24,
          height: 24,
          padding: 0,
          boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      />
    );
  };

  return (
    <div
      className={className}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        width: "100%",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
        ...style,
      }}
    >
      {header}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          gap: 12,
          overflow: "hidden",
          alignItems: "stretch",
        }}
      >
        {!leftCollapsed ? (
          <div
            style={{
              width: leftWidth,
              minWidth: leftWidth,
              flex: "0 0 auto",
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              position: "relative",
              transition: isDragging ? "none" : "width 0.3s, min-width 0.3s",
              flexShrink: 0,
              overflow: "hidden",
            }}
          >
            {leftPanel}
            <div
              onMouseDown={(e) => {
                e.preventDefault();
                setIsDragging("left");
              }}
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                right: -3,
                width: 6,
                cursor: "col-resize",
                zIndex: 10,
              }}
            >
              <div
                style={{
                  width: 2,
                  height: "100%",
                  background: isDragging === "left" ? "#3b82f6" : "transparent",
                  borderRadius: 1,
                }}
              />
            </div>
            <CollapseButton side="left" />
          </div>
        ) : (
          <div
            style={{
              width: collapsedWidth,
              minWidth: collapsedWidth,
              minHeight: 0,
              alignSelf: "stretch",
              position: "relative",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              background: "#fafafa",
              border: "1px solid #e8e8e8",
              borderRadius: 4,
              flexShrink: 0,
            }}
          >
            <CollapseButton side="left" />
          </div>
        )}

        <div
          className="three-column-layout-middle"
          style={{
            flex: 1,
            minWidth: 300,
            minHeight: 0,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {middlePanel}
        </div>

        {!rightCollapsed ? (
          <div
            style={{
              width: rightWidth,
              minWidth: rightWidth,
              flex: "0 0 auto",
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              position: "relative",
              transition: isDragging ? "none" : "width 0.3s, min-width 0.3s",
              flexShrink: 0,
              overflow: "hidden",
            }}
          >
            {rightPanel}
            <div
              onMouseDown={(e) => {
                e.preventDefault();
                setIsDragging("right");
              }}
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                left: -3,
                width: 6,
                cursor: "col-resize",
                zIndex: 10,
              }}
            >
              <div
                style={{
                  width: 2,
                  height: "100%",
                  background: isDragging === "right" ? "#3b82f6" : "transparent",
                  borderRadius: 1,
                }}
              />
            </div>
            <CollapseButton side="right" />
          </div>
        ) : (
          <div
            style={{
              width: collapsedWidth,
              minWidth: collapsedWidth,
              minHeight: 0,
              alignSelf: "stretch",
              position: "relative",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              background: "#fafafa",
              border: "1px solid #e8e8e8",
              borderRadius: 4,
              flexShrink: 0,
            }}
          >
            <CollapseButton side="right" />
          </div>
        )}
      </div>
    </div>
  );
};

export default ThreeColumnLayout;
