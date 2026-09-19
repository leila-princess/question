export const ROLE_KM_MANAGER = '试验知识管理人员';

export const ROLE_OPTIONS = ['系统管理员', ROLE_KM_MANAGER, '试验人员'] as const;

export type RoleType = typeof ROLE_OPTIONS[number];

/** 历史角色名兼容 */
const ROLE_ALIASES: Record<string, string> = {
  试验管理人员: ROLE_KM_MANAGER,
};

export function normalizeUserRole(userRole: string): string {
  const role = (userRole || '').trim();
  return ROLE_ALIASES[role] || role;
}

export interface MenuPermission {
  menu: string[];
  submenu: Record<string, string[]>;
}

const ROLE_PERMISSIONS: Record<string, MenuPermission> = {
  '系统管理员': {
    menu: ['home', 'GLCX', 'FZSJ', 'FLGL', 'WJGL', 'ZSTPZS', 'KSHZS', 'XTGL'],
    submenu: {
      'XTGL': ['YHGL', 'SJKGL', 'MXGL', 'SJBF', 'RZGL'],
    },
  },
  [ROLE_KM_MANAGER]: {
    menu: ['home', 'GLCX', 'FZSJ', 'FLGL', 'WJGL', 'ZSTPZS', 'KSHZS', 'XTGL'],
    submenu: {
      'XTGL': ['YHGL', 'MXGL'],
    },
  },
  '试验人员': {
    menu: ['home', 'GLCX', 'FZSJ', 'ZSTPZS', 'KSHZS'],
    submenu: {},
  },
};

export function getMenuPermission(userRole: string): MenuPermission {
  const role = normalizeUserRole(userRole);
  return ROLE_PERMISSIONS[role] || ROLE_PERMISSIONS['试验人员'];
}

export function hasMenuAccess(userRole: string, menuKey: string): boolean {
  const perm = getMenuPermission(userRole);
  return perm.menu.includes(menuKey);
}

export function hasSubmenuAccess(userRole: string, parentKey: string, childKey: string): boolean {
  const perm = getMenuPermission(userRole);
  const allowed = perm.submenu[parentKey];
  if (!allowed) return true;
  return allowed.includes(childKey);
}

/** 子菜单项 -> 父级菜单 key */
const SUBMENU_PARENT: Record<string, string> = {
  JQCX: 'GLCX',
  SSYQ: 'GLCX',
  ZNWD: 'GLCX',
  TPCX: 'GLCX',
  JYGZ: 'FZSJ',
  JYLLM: 'FZSJ',
  YHGL: 'XTGL',
  SJKGL: 'XTGL',
  MXGL: 'XTGL',
  SJBF: 'XTGL',
  RZGL: 'XTGL',
  WTGL: 'XTGL',
  WJGL1: 'ZSGL',
  BQGL: 'ZSGL',
};

export function canAccessPage(userRole: string, pageKey: string): boolean {
  if (!pageKey) return true;
  const parent = SUBMENU_PARENT[pageKey];
  if (parent) {
    return hasMenuAccess(userRole, parent) && hasSubmenuAccess(userRole, parent, pageKey);
  }
  return hasMenuAccess(userRole, pageKey);
}

export type MenuItemLike = {
  key?: string | number;
  children?: MenuItemLike[];
};

/** 按角色过滤 Ant Design Menu items */
export function filterMenuByRole<T extends MenuItemLike>(
  items: (T | null | undefined)[],
  userRole: string,
): T[] {
  const role = normalizeUserRole(userRole || '试验人员');
  return items
    .filter((item): item is T => item != null && item.key != null && hasMenuAccess(role, String(item.key)))
    .map((item) => {
      if (!item.children?.length) return item;
      const parentKey = String(item.key);
      const children = item.children.filter(
        (child) => child?.key != null && hasSubmenuAccess(role, parentKey, String(child.key)),
      );
      return { ...item, children };
    })
    .filter((item) => !item.children || item.children.length > 0);
}
