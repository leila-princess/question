import re

with open('fronted/app/FZSJ/JYGZ/page.tsx', 'r') as f:
    content = f.read()

# Replace the environment collection logic
old = '    const projectEnvNames = new Set<string>();\n    if (data.tableRows && data.tableRows.length > 0) {\n      data.tableRows.forEach((row: any) => {\n        Object.keys(row.projectsByEnv || {}).forEach((envName: string) => {\n          const projects = row.projectsByEnv[envName] || [];\n          projects.forEach((proj: any) => {\n            const isSelected = data.selectedProjects.some((p: any) => p.projectId === proj.id);\n            if (isSelected) {\n              projectEnvNames.add(envName);\n            }\n          });\n        });\n      });\n    }\n\n    const newEnvs: EnvironmentConfig[] = [];\n    const uniqueEnvNames = Array.from(projectEnvNames);\n    for (let i = 0; i < uniqueEnvNames.length; i++) {\n      const envName = uniqueEnvNames[i];\n      const m = allEnvironments.find(e => e.name === envName);\n      if (m) newEnvs.push({ env_id: m.id, env_name: m.name });\n    }'

new = '    const envNames = new Set<string>();\n    config.selected_projects.forEach((projectId) => {\n      const project = config.projects_data.find(p => p.id === projectId);\n      if (project?.env_name) envNames.add(project.env_name);\n    });\n    config.selected_indicators.forEach((indicatorId) => {\n      const indicator = config.indicators_data.find(i => i.id === indicatorId);\n      if (indicator?.env_name) envNames.add(indicator.env_name);\n    });\n    if (envNames.size === 0 && config.environments.length > 0) {\n      envNames.add(config.environments[0].env_name);\n    }\n    const newEnvs: EnvironmentConfig[] = [];\n    envNames.forEach((envName) => {\n      const m = allEnvironments.find(e => e.name === envName);\n      if (m) newEnvs.push({ env_id: m.id, env_name: m.name });\n    });'

content = content.replace(old, new)

with open('fronted/app/FZSJ/JYGZ/page.tsx', 'w') as f:
    f.write(content)
print('Fixed!')