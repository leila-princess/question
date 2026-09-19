import React from 'react';
import { Empty } from 'antd';
import './Charts.css';

// 柱状图组件
export const BarChart = ({ 
  data, 
  xField, 
  yField, 
  isStack = false, 
  color, 
  colors,
  title,
  height = 400 
}) => {
  if (!data || data.length === 0) {
    return <Empty description="暂无数据" />;
  }

  const isMultiSeries = Array.isArray(yField);
  const maxValue = Math.max(
    ...data.map(item => 
      isMultiSeries 
        ? yField.reduce((sum, field) => sum + (item[field] || 0), 0)
        : item[yField] || 0
    )
  );

  return (
    <div className="chart-container" style={{ height }}>
      {title && <div className="chart-title">{title}</div>}
      <div className="bar-chart">
        {data.map((item, index) => (
          <div key={index} className="bar-group">
            <div className="x-label">{item[xField]}</div>
            <div className="bar-container">
              {isMultiSeries ? (
                yField.map((field, fieldIndex) => {
                  const value = item[field] || 0;
                  const percentage = maxValue ? (value / maxValue) * 100 : 0;
                  return (
                    <div
                      key={fieldIndex}
                      className="bar-segment"
                      style={{
                        height: `${percentage}%`,
                        backgroundColor: colors ? colors[fieldIndex] : getColor(fieldIndex),
                        width: `${100 / yField.length}%`
                      }}
                      title={`${field}: ${value}`}
                    />
                  );
                })
              ) : (
                <div
                  className="bar"
                  style={{
                    height: `${maxValue ? (item[yField] / maxValue) * 100 : 0}%`,
                    backgroundColor: color || '#1890ff'
                  }}
                  title={`${item[yField]}`}
                />
              )}
            </div>
          </div>
        ))}
      </div>
      {isMultiSeries && (
        <div className="chart-legend">
          {yField.map((field, index) => (
            <div key={index} className="legend-item">
              <span 
                className="legend-color" 
                style={{ backgroundColor: colors ? colors[index] : getColor(index) }}
              />
              <span className="legend-label">{field}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// 折线图组件
export const LineChart = ({ 
  data, 
  xField, 
  yField, 
  smooth = true,
  title,
  height = 400 
}) => {
  if (!data || data.length === 0) {
    return <Empty description="暂无数据" />;
  }

  const isMultiSeries = Array.isArray(yField);
  const maxValue = Math.max(
    ...data.flatMap(item => 
      isMultiSeries 
        ? yField.map(field => item[field] || 0)
        : [item[yField] || 0]
    )
  );

  const svgWidth = 800;
  const svgHeight = 300;
  const padding = 60;
  const chartWidth = svgWidth - padding * 2;
  const chartHeight = svgHeight - padding * 2;

  const getPointX = (index) => padding + (index / (data.length - 1)) * chartWidth;
  const getPointY = (value) => svgHeight - padding - (value / maxValue) * chartHeight;

  return (
    <div className="chart-container" style={{ height }}>
      {title && <div className="chart-title">{title}</div>}
      <svg width="100%" height="90%" viewBox={`0 0 ${svgWidth} ${svgHeight}`}>
        {/* 网格线 */}
        <g className="grid-lines">
          {[0, 0.25, 0.5, 0.75, 1].map((ratio, i) => (
            <g key={i}>
              <line
                x1={padding}
                y1={getPointY(maxValue * ratio)}
                x2={svgWidth - padding}
                y2={getPointY(maxValue * ratio)}
                stroke="#e8e8e8"
                strokeWidth="1"
              />
              <text
                x={padding - 10}
                y={getPointY(maxValue * ratio)}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="12"
                fill="#666"
              >
                {Math.round(maxValue * ratio)}
              </text>
            </g>
          ))}
        </g>

        {/* X轴标签 */}
        {data.map((item, index) => (
          <text
            key={`label-${index}`}
            x={getPointX(index)}
            y={svgHeight - padding + 20}
            textAnchor="middle"
            fontSize="12"
            fill="#666"
          >
            {item[xField]}
          </text>
        ))}

        {/* 折线 */}
        {isMultiSeries ? (
          yField.map((field, fieldIndex) => {
            const points = data
              .map((item, index) => `${getPointX(index)},${getPointY(item[field] || 0)}`)
              .join(' ');

            return (
              <g key={fieldIndex}>
                <polyline
                  points={points}
                  fill="none"
                  stroke={getColor(fieldIndex)}
                  strokeWidth="2"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
                {data.map((item, index) => (
                  <circle
                    key={`point-${fieldIndex}-${index}`}
                    cx={getPointX(index)}
                    cy={getPointY(item[field] || 0)}
                    r="4"
                    fill={getColor(fieldIndex)}
                    stroke="#fff"
                    strokeWidth="2"
                  />
                ))}
              </g>
            );
          })
        ) : (
          <g>
            <polyline
              points={data
                .map((item, index) => `${getPointX(index)},${getPointY(item[yField] || 0)}`)
                .join(' ')}
              fill="none"
              stroke="#1890ff"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {data.map((item, index) => (
              <circle
                key={`point-${index}`}
                cx={getPointX(index)}
                cy={getPointY(item[yField] || 0)}
                r="4"
                fill="#1890ff"
                stroke="#fff"
                strokeWidth="2"
              />
            ))}
          </g>
        )}
      </svg>

      {isMultiSeries && (
        <div className="chart-legend">
          {yField.map((field, index) => (
            <div key={index} className="legend-item">
              <span 
                className="legend-color" 
                style={{ backgroundColor: getColor(index) }}
              />
              <span className="legend-label">{field}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// 饼图组件
export const PieChart = ({ 
  data, 
  angleField, 
  colorField, 
  colors,
  title,
  height = 400 
}) => {
  if (!data || data.length === 0) {
    return <Empty description="暂无数据" />;
  }

  const total = data.reduce((sum, item) => sum + (item[angleField] || 0), 0);
  const radius = 100;
  const centerX = 150;
  const centerY = 150;

  let currentAngle = 0;

  return (
    <div className="chart-container" style={{ height }}>
      {title && <div className="chart-title">{title}</div>}
      <div className="pie-chart-container">
        <svg width="300" height="300" viewBox="0 0 300 300">
          {data.map((item, index) => {
            const value = item[angleField] || 0;
            const percentage = total ? (value / total) * 100 : 0;
            const angle = (value / total) * 360;
            
            const startAngle = currentAngle;
            const endAngle = currentAngle + angle;
            currentAngle = endAngle;

            // 计算扇形路径
            const startRad = (startAngle - 90) * (Math.PI / 180);
            const endRad = (endAngle - 90) * (Math.PI / 180);
            
            const x1 = centerX + radius * Math.cos(startRad);
            const y1 = centerY + radius * Math.sin(startRad);
            const x2 = centerX + radius * Math.cos(endRad);
            const y2 = centerY + radius * Math.sin(endRad);

            const largeArcFlag = angle > 180 ? 1 : 0;

            const pathData = [
              `M ${centerX} ${centerY}`,
              `L ${x1} ${y1}`,
              `A ${radius} ${radius} 0 ${largeArcFlag} 1 ${x2} ${y2}`,
              'Z'
            ].join(' ');

            // 计算标签位置
            const midAngle = startAngle + angle / 2;
            const labelRadius = radius * 0.7;
            const labelX = centerX + labelRadius * Math.cos((midAngle - 90) * (Math.PI / 180));
            const labelY = centerY + labelRadius * Math.sin((midAngle - 90) * (Math.PI / 180));

            return (
              <g key={index}>
                <path
                  d={pathData}
                  fill={colors ? colors[index] : getColor(index)}
                  stroke="#fff"
                  strokeWidth="2"
                  className="pie-slice"
                />
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize="12"
                  fill="#333"
                  fontWeight="bold"
                >
                  {percentage.toFixed(1)}%
                </text>
              </g>
            );
          })}
        </svg>
        
        <div className="pie-legend">
          {data.map((item, index) => {
            const value = item[angleField] || 0;
            const percentage = total ? (value / total) * 100 : 0;
            
            return (
              <div key={index} className="pie-legend-item">
                <span 
                  className="legend-color" 
                  style={{ backgroundColor: colors ? colors[index] : getColor(index) }}
                />
                <div className="legend-content">
                  <div className="legend-label">{item[colorField]}</div>
                  <div className="legend-value">
                    {value} ({percentage.toFixed(1)}%)
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

// 环状图组件
export const DoughnutChart = ({ 
  data, 
  angleField, 
  colorField, 
  colors,
  title,
  height = 400 
}) => {
  if (!data || data.length === 0) {
    return <Empty description="暂无数据" />;
  }

  const total = data.reduce((sum, item) => sum + (item[angleField] || 0), 0);
  const radius = 80;
  const innerRadius = 40;
  const centerX = 150;
  const centerY = 150;

  let currentAngle = 0;

  return (
    <div className="chart-container" style={{ height }}>
      {title && <div className="chart-title">{title}</div>}
      <div className="doughnut-chart-container">
        <svg width="300" height="300" viewBox="0 0 300 300">
          {data.map((item, index) => {
            const value = item[angleField] || 0;
            const angle = (value / total) * 360;
            
            const startAngle = currentAngle;
            const endAngle = currentAngle + angle;
            currentAngle = endAngle;

            // 计算扇形路径（环形）
            const startRad = (startAngle - 90) * (Math.PI / 180);
            const endRad = (endAngle - 90) * (Math.PI / 180);
            
            const x1 = centerX + innerRadius * Math.cos(startRad);
            const y1 = centerY + innerRadius * Math.sin(startRad);
            const x2 = centerX + innerRadius * Math.cos(endRad);
            const y2 = centerY + innerRadius * Math.sin(endRad);
            const x3 = centerX + radius * Math.cos(endRad);
            const y3 = centerY + radius * Math.sin(endRad);
            const x4 = centerX + radius * Math.cos(startRad);
            const y4 = centerY + radius * Math.sin(startRad);

            const largeArcFlag = angle > 180 ? 1 : 0;

            const pathData = [
              `M ${x1} ${y1}`,
              `L ${x4} ${y4}`,
              `A ${radius} ${radius} 0 ${largeArcFlag} 1 ${x3} ${y3}`,
              `L ${x2} ${y2}`,
              `A ${innerRadius} ${innerRadius} 0 ${largeArcFlag} 0 ${x1} ${y1}`,
              'Z'
            ].join(' ');

            // 计算中心文本
            const centerText = `${total}`;

            return (
              <g key={index}>
                <path
                  d={pathData}
                  fill={colors ? colors[index] : getColor(index)}
                  stroke="#fff"
                  strokeWidth="2"
                  className="doughnut-slice"
                />
              </g>
            );
          })}
          
          {/* 中心文本 */}
          <circle cx={centerX} cy={centerY} r={innerRadius - 5} fill="#fff" />
          <text
            x={centerX}
            y={centerY}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize="14"
            fill="#666"
          >
            总计
          </text>
          <text
            x={centerX}
            y={centerY + 20}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize="16"
            fill="#333"
            fontWeight="bold"
          >
            {total}
          </text>
        </svg>
        
        <div className="doughnut-legend">
          {data.map((item, index) => {
            const value = item[angleField] || 0;
            const percentage = total ? (value / total) * 100 : 0;
            
            return (
              <div key={index} className="legend-item">
                <span 
                  className="legend-color" 
                  style={{ backgroundColor: colors ? colors[index] : getColor(index) }}
                />
                <div className="legend-content">
                  <div className="legend-label">{item[colorField]}</div>
                  <div className="legend-value">{percentage.toFixed(1)}%</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

// 雷达图组件
export const RadarChart = ({ 
  data, 
  angleField, 
  radiusField, 
  title,
  height = 400 
}) => {
  if (!data || data.length === 0) {
    return <Empty description="暂无数据" />;
  }

  const isMultiSeries = Array.isArray(radiusField);
  const categories = data.map(item => item[angleField]);
  const maxValue = Math.max(
    ...data.flatMap(item => 
      isMultiSeries 
        ? radiusField.map(field => item[field] || 0)
        : [item[radiusField] || 0]
    )
  );

  const radius = 100;
  const centerX = 150;
  const centerY = 150;
  const angleStep = (2 * Math.PI) / categories.length;

  return (
    <div className="chart-container" style={{ height }}>
      {title && <div className="chart-title">{title}</div>}
      <svg width="300" height="300" viewBox="0 0 300 300">
        {/* 网格线 */}
        {[0.2, 0.4, 0.6, 0.8, 1].map((scale, i) => {
          const points = categories.map((_, index) => {
            const angle = index * angleStep - Math.PI / 2;
            const x = centerX + radius * scale * Math.cos(angle);
            const y = centerY + radius * scale * Math.sin(angle);
            return `${x},${y}`;
          }).join(' ');

          return (
            <polygon
              key={i}
              points={points}
              fill="none"
              stroke="#e8e8e8"
              strokeWidth="1"
            />
          );
        })}

        {/* 轴线 */}
        {categories.map((category, index) => {
          const angle = index * angleStep - Math.PI / 2;
          const x = centerX + radius * Math.cos(angle);
          const y = centerY + radius * Math.sin(angle);

          return (
            <g key={index}>
              <line
                x1={centerX}
                y1={centerY}
                x2={x}
                y2={y}
                stroke="#e8e8e8"
                strokeWidth="1"
              />
              <text
                x={centerX + (radius + 20) * Math.cos(angle)}
                y={centerY + (radius + 20) * Math.sin(angle)}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize="12"
                fill="#666"
              >
                {category}
              </text>
            </g>
          );
        })}

        {/* 数据系列 */}
        {isMultiSeries ? (
          radiusField.map((field, fieldIndex) => {
            const points = data.map((item, index) => {
              const value = item[field] || 0;
              const angle = index * angleStep - Math.PI / 2;
              const scale = maxValue ? value / maxValue : 0;
              const x = centerX + radius * scale * Math.cos(angle);
              const y = centerY + radius * scale * Math.sin(angle);
              return `${x},${y}`;
            }).join(' ');

            return (
              <g key={fieldIndex}>
                <polygon
                  points={points}
                  fill={getColor(fieldIndex)}
                  fillOpacity="0.3"
                  stroke={getColor(fieldIndex)}
                  strokeWidth="2"
                />
                {data.map((item, index) => {
                  const value = item[field] || 0;
                  const angle = index * angleStep - Math.PI / 2;
                  const scale = maxValue ? value / maxValue : 0;
                  const x = centerX + radius * scale * Math.cos(angle);
                  const y = centerY + radius * scale * Math.sin(angle);

                  return (
                    <circle
                      key={`${fieldIndex}-${index}`}
                      cx={x}
                      cy={y}
                      r="4"
                      fill={getColor(fieldIndex)}
                      stroke="#fff"
                      strokeWidth="2"
                    />
                  );
                })}
              </g>
            );
          })
        ) : (
          <g>
            <polygon
              points={data.map((item, index) => {
                const value = item[radiusField] || 0;
                const angle = index * angleStep - Math.PI / 2;
                const scale = maxValue ? value / maxValue : 0;
                const x = centerX + radius * scale * Math.cos(angle);
                const y = centerY + radius * scale * Math.sin(angle);
                return `${x},${y}`;
              }).join(' ')}
              fill="#1890ff"
              fillOpacity="0.3"
              stroke="#1890ff"
              strokeWidth="2"
            />
            {data.map((item, index) => {
              const value = item[radiusField] || 0;
              const angle = index * angleStep - Math.PI / 2;
              const scale = maxValue ? value / maxValue : 0;
              const x = centerX + radius * scale * Math.cos(angle);
              const y = centerY + radius * scale * Math.sin(angle);

              return (
                <circle
                  key={index}
                  cx={x}
                  cy={y}
                  r="4"
                  fill="#1890ff"
                  stroke="#fff"
                  strokeWidth="2"
                />
              );
            })}
          </g>
        )}
      </svg>

      {isMultiSeries && (
        <div className="chart-legend">
          {radiusField.map((field, index) => (
            <div key={index} className="legend-item">
              <span 
                className="legend-color" 
                style={{ backgroundColor: getColor(index) }}
              />
              <span className="legend-label">{field}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// 通用颜色生成函数
const getColor = (index) => {
  const colors = [
    '#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1',
    '#fa541c', '#13c2c2', '#eb2f96', '#a0d911', '#2f54eb'
  ];
  return colors[index % colors.length];
};

export default {
  BarChart,
  LineChart,
  PieChart,
  DoughnutChart,
  RadarChart
};