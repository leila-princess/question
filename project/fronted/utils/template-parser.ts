// utils/template-parser.ts

export interface TemplateSection {
  id: string;
  title: string;
  type: 'text' | 'number' | 'select' | 'checkbox' | 'file';
  required: boolean;
  options?: string[];
  placeholder?: string;
  defaultValue?: any;
  description?: string;
}

export interface TemplateStructure {
  id: string;
  name: string;
  description: string;
  category: string;
  sections: TemplateSection[];
  compatibleStandards: string[];
  requiredParameters: string[];
  fields: any[];
}

// 模拟解析上传的模板文件（在实际应用中，这里会调用后端API解析真实文件）
export const parseTemplateFile = async (file: File): Promise<TemplateStructure | null> => {
  // 根据文件名猜测模板类型（实际应用中应解析文件内容）
  const fileName = file.name.toLowerCase();
  
  if (fileName.includes('拉伸') || fileName.includes('tensile')) {
    return tensileTestTemplate;
  } else if (fileName.includes('压缩') || fileName.includes('compression')) {
    return compressionTestTemplate;
  } else if (fileName.includes('疲劳') || fileName.includes('fatigue')) {
    return fatigueTestTemplate;
  } else if (fileName.includes('弯曲') || fileName.includes('bend')) {
    return bendingTestTemplate;
  } else {
    // 默认返回通用模板
    return generalTestTemplate;
  }
};

// 预定义的模板类型
export const tensileTestTemplate: TemplateStructure = {
  id: 'tensile',
  name: '拉伸试验模板',
  description: '用于材料拉伸性能测试的标准模板',
  category: '力学性能',
  sections: [
    {
      id: 'sample_dimensions',
      title: '试样尺寸',
      type: 'select',
      required: true,
      options: ['标准矩形试样', '标准圆形试样', '非标定制试样'],
      description: '选择试样形状和尺寸标准'
    },
    {
      id: 'gauge_length',
      title: '标距长度',
      type: 'number',
      required: true,
      placeholder: '输入标距长度 (mm)',
      defaultValue: 50
    },
    {
      id: 'cross_section',
      title: '横截面积',
      type: 'number',
      required: true,
      placeholder: '输入初始横截面积 (mm²)'
    },
    {
      id: 'loading_speed',
      title: '加载速度',
      type: 'select',
      required: true,
      options: ['1 mm/min', '2 mm/min', '5 mm/min', '10 mm/min', '应力控制模式'],
      description: '选择试验加载速度'
    },
    {
      id: 'temperature_control',
      title: '温度控制',
      type: 'checkbox',
      required: false,
      options: ['室温试验', '高温试验', '低温试验', '变温试验'],
      description: '选择试验温度条件'
    },
    {
      id: 'data_acquisition',
      title: '数据采集参数',
      type: 'text',
      required: false,
      placeholder: '如：采样频率 10Hz，记录位移和力',
      description: '数据采集系统配置'
    }
  ],
  compatibleStandards: [
    'GB/T 228.1-2021',
    'ISO 6892-1',
    'ASTM E8/E8M',
    'JIS Z 2241'
  ],
  requiredParameters: ['试样尺寸', '加载速率', '试验温度', '数据采集频率'],
  fields: []
};

export const compressionTestTemplate: TemplateStructure = {
  id: 'compression',
  name: '压缩试验模板',
  description: '用于材料压缩性能测试的标准模板',
  category: '力学性能',
  sections: [
    {
      id: 'specimen_shape',
      title: '试样形状',
      type: 'select',
      required: true,
      options: ['圆柱形', '立方体', '矩形棱柱', '特殊形状'],
      description: '选择压缩试样形状'
    },
    {
      id: 'aspect_ratio',
      title: '长径比',
      type: 'number',
      required: true,
      placeholder: '输入长径比 (如 2:1)',
      defaultValue: 2
    },
    {
      id: 'alignment',
      title: '对中要求',
      type: 'select',
      required: true,
      options: ['标准对中', '精密对中', '特殊对中要求'],
      description: '选择试样对中精度要求'
    },
    {
      id: 'strain_rate',
      title: '应变速率',
      type: 'number',
      required: true,
      placeholder: '输入应变速率 (s⁻¹)',
      defaultValue: 0.001
    },
    {
      id: 'friction_control',
      title: '摩擦控制',
      type: 'checkbox',
      required: false,
      options: ['润滑端面', '防滑处理', '标准端面'],
      description: '端面摩擦控制方法'
    }
  ],
  compatibleStandards: [
    'GB/T 7314-2017',
    'ISO 604',
    'ASTM E9',
    'JIS Z 2204'
  ],
  requiredParameters: ['试样形状', '长径比', '应变速率', '端面条件'],
  fields: []
};

export const fatigueTestTemplate: TemplateStructure = {
  id: 'fatigue',
  name: '疲劳试验模板',
  description: '用于材料疲劳性能测试的标准模板',
  category: '耐久性',
  sections: [
    {
      id: 'loading_mode',
      title: '加载模式',
      type: 'select',
      required: true,
      options: ['拉-拉', '拉-压', '三点弯曲', '四点弯曲', '扭转'],
      description: '选择疲劳加载模式'
    },
    {
      id: 'stress_ratio',
      title: '应力比',
      type: 'number',
      required: true,
      placeholder: '输入应力比 R (如 0.1)',
      defaultValue: 0.1
    },
    {
      id: 'frequency',
      title: '试验频率',
      type: 'number',
      required: true,
      placeholder: '输入试验频率 (Hz)',
      defaultValue: 10
    },
    {
      id: 'cycle_target',
      title: '循环次数目标',
      type: 'select',
      required: true,
      options: ['10⁴ 循环', '10⁵ 循环', '10⁶ 循环', '10⁷ 循环', '直至失效'],
      description: '试验循环次数目标'
    },
    {
      id: 'environment',
      title: '环境条件',
      type: 'checkbox',
      required: false,
      options: ['室温空气', '高温环境', '腐蚀环境', '真空环境'],
      description: '试验环境条件'
    }
  ],
  compatibleStandards: [
    'GB/T 3075-2021',
    'ISO 1099',
    'ASTM E466',
    'JIS Z 2273'
  ],
  requiredParameters: ['加载模式', '应力比', '频率', '循环目标'],
  fields: []
};

export const bendingTestTemplate: TemplateStructure = {
  id: 'bending',
  name: '弯曲试验模板',
  description: '用于材料弯曲性能测试的标准模板',
  category: '力学性能',
  sections: [
    {
      id: 'bending_type',
      title: '弯曲类型',
      type: 'select',
      required: true,
      options: ['三点弯曲', '四点弯曲', '纯弯曲'],
      description: '选择弯曲试验类型'
    },
    {
      id: 'span_length',
      title: '跨距长度',
      type: 'number',
      required: true,
      placeholder: '输入跨距长度 (mm)'
    },
    {
      id: 'deflection_rate',
      title: '挠度速率',
      type: 'number',
      required: true,
      placeholder: '输入挠度速率 (mm/min)',
      defaultValue: 1
    },
    {
      id: 'support_condition',
      title: '支撑条件',
      type: 'checkbox',
      required: false,
      options: ['自由支撑', '固定支撑', '滚动支撑'],
      description: '试样支撑条件'
    },
    {
      id: 'strain_measurement',
      title: '应变测量',
      type: 'select',
      required: false,
      options: ['位移计测量', '应变片测量', '光学测量', '无测量'],
      description: '应变测量方法'
    }
  ],
  compatibleStandards: [
    'GB/T 1449-2005',
    'ISO 178',
    'ASTM D790',
    'JIS K 7171'
  ],
  requiredParameters: ['弯曲类型', '跨距长度', '挠度速率', '支撑条件'],
  fields: []
};

export const generalTestTemplate: TemplateStructure = {
  id: 'general',
  name: '通用试验模板',
  description: '适用于多种试验类型的通用模板',
  category: '通用',
  sections: [
    {
      id: 'test_objective',
      title: '试验目的',
      type: 'text',
      required: true,
      placeholder: '描述试验的主要目的和目标'
    },
    {
      id: 'sample_description',
      title: '试样描述',
      type: 'text',
      required: true,
      placeholder: '详细描述试样信息'
    },
    {
      id: 'equipment_requirements',
      title: '设备要求',
      type: 'text',
      required: true,
      placeholder: '试验所需设备和仪器'
    },
    {
      id: 'environmental_conditions',
      title: '环境条件',
      type: 'text',
      required: false,
      placeholder: '试验环境条件要求'
    },
    {
      id: 'safety_requirements',
      title: '安全要求',
      type: 'text',
      required: false,
      placeholder: '试验安全注意事项'
    }
  ],
  compatibleStandards: [
    '通用标准'
  ],
  requiredParameters: ['试验目的', '试样描述', '设备要求'],
  fields: []
};

// 获取所有可用的模板类型
export const getAllTemplates = (): TemplateStructure[] => {
  return [
    tensileTestTemplate,
    compressionTestTemplate,
    fatigueTestTemplate,
    bendingTestTemplate,
    generalTestTemplate
  ];
};

// 根据模板ID获取模板
export const getTemplateById = (id: string): TemplateStructure | null => {
  const templates = getAllTemplates();
  return templates.find(template => template.id === id) || null;
};