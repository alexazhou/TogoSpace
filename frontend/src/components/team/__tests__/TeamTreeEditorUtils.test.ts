import { describe, it, expect } from 'vitest';

// 从 TeamTreeEditor.vue 提取的纯函数逻辑，便于独立测试
// 这些函数不依赖组件状态，可以安全地提取和测试

function createDraftNodeId(prefix = 'node'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function parseDriverTypeValue(driver: string): string {
  const normalized = driver.trim().toUpperCase();
  if (normalized === 'NATIVE' || normalized === 'CLAUDE_SDK' || normalized === 'TSP') {
    return normalized;
  }
  return '';
}

function isOnBoardAgent(employStatus: string | undefined): boolean {
  return employStatus === 'ON_BOARD' || !employStatus;
}

function createDepartmentNameAllocator(
  newDeptPrefix: string,
  initialDepartmentNames: string[] = [],
): () => string {
  const usedDepartmentNames = new Set<string>();
  let maxDepartmentIndex = 0;

  initialDepartmentNames.forEach((departmentName) => {
    const trimmedDepartmentName = departmentName.trim();
    if (!trimmedDepartmentName) {
      return;
    }

    usedDepartmentNames.add(trimmedDepartmentName);
    const matched = trimmedDepartmentName.match(new RegExp(`^${newDeptPrefix}(\\d+)$`));
    if (!matched) {
      return;
    }

    maxDepartmentIndex = Math.max(maxDepartmentIndex, Number(matched[1]));
  });

  return () => {
    let nextDepartmentIndex = maxDepartmentIndex + 1;
    while (usedDepartmentNames.has(`${newDeptPrefix}${nextDepartmentIndex}`)) {
      nextDepartmentIndex += 1;
    }

    const nextDepartmentName = `${newDeptPrefix}${nextDepartmentIndex}`;
    usedDepartmentNames.add(nextDepartmentName);
    maxDepartmentIndex = nextDepartmentIndex;
    return nextDepartmentName;
  };
}

type DraftOrgNode = {
  id: string;
  kind: 'member' | 'pending';
  agentId: number | null;
  deptId: number | null;
  memberName: string;
  roleTemplateId: number | null;
  model: string;
  driver: string;
  employeeNumber: string;
  deptName: string;
  deptResponsibility: string;
  children: DraftOrgNode[];
};

function createPendingNode(): DraftOrgNode {
  return {
    id: createDraftNodeId('pending'),
    kind: 'pending',
    agentId: null,
    deptId: null,
    memberName: '',
    roleTemplateId: null,
    model: '',
    driver: '',
    employeeNumber: '',
    deptName: '',
    deptResponsibility: '',
    children: [],
  };
}

function cloneDraftOrgNode(node: DraftOrgNode | null): DraftOrgNode | null {
  if (!node) {
    return null;
  }

  return {
    ...node,
    children: node.children.map((child) => cloneDraftOrgNode(child)!),
  };
}

function findNodeById(root: DraftOrgNode | null, nodeId: string): DraftOrgNode | null {
  if (!root) {
    return null;
  }

  const stack = [root];
  while (stack.length) {
    const current = stack.pop()!;
    if (current.id === nodeId) {
      return current;
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      stack.push(current.children[index]);
    }
  }

  return null;
}

function collectMemberNodes(root: DraftOrgNode | null): DraftOrgNode[] {
  if (!root) {
    return [];
  }

  const result: DraftOrgNode[] = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop()!;
    if (current.kind === 'member' && current.memberName) {
      result.push(current);
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      stack.push(current.children[index]);
    }
  }

  return result;
}

function countManagedChildren(node: DraftOrgNode): number {
  return node.children.filter((child) => child.kind === 'member').length;
}

function removeNodeById(root: DraftOrgNode, nodeId: string): boolean {
  for (let index = 0; index < root.children.length; index += 1) {
    const child = root.children[index];
    if (child.id === nodeId) {
      root.children.splice(index, 1);
      return true;
    }
    if (removeNodeById(child, nodeId)) {
      return true;
    }
  }

  return false;
}

function treeHasPendingNode(root: DraftOrgNode | null): boolean {
  if (!root) {
    return false;
  }

  const stack = [root];
  while (stack.length) {
    const current = stack.pop()!;
    if (current.kind === 'pending') {
      return true;
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      stack.push(current.children[index]);
    }
  }

  return false;
}

describe('TeamTreeEditor utility functions', () => {
  describe('createDraftNodeId', () => {
    it('generates unique node IDs', () => {
      const id1 = createDraftNodeId('node');
      const id2 = createDraftNodeId('node');
      expect(id1).not.toBe(id2);
      expect(id1.startsWith('node-')).toBe(true);
      expect(id2.startsWith('node-')).toBe(true);
    });

    it('uses custom prefix', () => {
      const id = createDraftNodeId('pending');
      expect(id.startsWith('pending-')).toBe(true);
    });
  });

  describe('parseDriverTypeValue', () => {
    it('returns valid driver types', () => {
      expect(parseDriverTypeValue('native')).toBe('NATIVE');
      expect(parseDriverTypeValue('NATIVE')).toBe('NATIVE');
      expect(parseDriverTypeValue('  native  ')).toBe('NATIVE');
      expect(parseDriverTypeValue('claude_sdk')).toBe('CLAUDE_SDK');
      expect(parseDriverTypeValue('tsp')).toBe('TSP');
    });

    it('returns empty for invalid driver types', () => {
      expect(parseDriverTypeValue('unknown')).toBe('');
      expect(parseDriverTypeValue('')).toBe('');
      expect(parseDriverTypeValue('Native')).toBe('NATIVE'); // lowercase conversion
    });
  });

  describe('isOnBoardAgent', () => {
    it('returns true for ON_BOARD status', () => {
      expect(isOnBoardAgent('ON_BOARD')).toBe(true);
    });

    it('returns true for undefined status', () => {
      expect(isOnBoardAgent(undefined)).toBe(true);
    });

    it('returns false for other statuses', () => {
      expect(isOnBoardAgent('OFF_BOARD')).toBe(false);
      expect(isOnBoardAgent('PENDING')).toBe(false);
    });
  });

  describe('createDepartmentNameAllocator', () => {
    it('generates sequential department names', () => {
      const allocator = createDepartmentNameAllocator('部门');
      expect(allocator()).toBe('部门1');
      expect(allocator()).toBe('部门2');
      expect(allocator()).toBe('部门3');
    });

    it('skips existing department names', () => {
      // maxDepartmentIndex = 3, next starts from 4
      const allocator = createDepartmentNameAllocator('部门', ['部门1', '部门3']);
      expect(allocator()).toBe('部门4');
      expect(allocator()).toBe('部门5');
    });

    it('handles non-standard department names', () => {
      const allocator = createDepartmentNameAllocator('部门', ['研发部', '部门5']);
      expect(allocator()).toBe('部门6');
    });

    it('ignores empty department names', () => {
      // maxDepartmentIndex = 2 (from '部门2'), empty strings are skipped
      const allocator = createDepartmentNameAllocator('部门', ['', '  ', '部门2']);
      expect(allocator()).toBe('部门3');
      expect(allocator()).toBe('部门4');
    });
  });

  describe('createPendingNode', () => {
    it('creates a pending node with correct structure', () => {
      const node = createPendingNode();
      expect(node.kind).toBe('pending');
      expect(node.id.startsWith('pending-')).toBe(true);
      expect(node.agentId).toBeNull();
      expect(node.memberName).toBe('');
      expect(node.children).toEqual([]);
    });
  });

  describe('cloneDraftOrgNode', () => {
    it('clones null as null', () => {
      expect(cloneDraftOrgNode(null)).toBeNull();
    });

    it('deep clones node with children', () => {
      const original: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: 'gpt-4',
        driver: 'native',
        employeeNumber: '1',
        deptName: '研发部',
        deptResponsibility: '开发',
        children: [
          {
            id: 'child',
            kind: 'member',
            agentId: 2,
            deptId: null,
            memberName: 'bob',
            roleTemplateId: 2,
            model: '',
            driver: '',
            employeeNumber: '',
            deptName: '',
            deptResponsibility: '',
            children: [],
          },
        ],
      };

      const cloned = cloneDraftOrgNode(original)!;
      expect(cloned.id).toBe('root');
      expect(cloned.memberName).toBe('alice');
      expect(cloned.children.length).toBe(1);
      expect(cloned.children[0].memberName).toBe('bob');

      // Verify deep clone - modifying clone doesn't affect original
      cloned.memberName = 'modified';
      cloned.children[0].memberName = 'modified_child';
      expect(original.memberName).toBe('alice');
      expect(original.children[0].memberName).toBe('bob');
    });
  });

  describe('findNodeById', () => {
    const root: DraftOrgNode = {
      id: 'root',
      kind: 'member',
      agentId: 1,
      deptId: null,
      memberName: 'alice',
      roleTemplateId: 1,
      model: '',
      driver: '',
      employeeNumber: '',
      deptName: '',
      deptResponsibility: '',
      children: [
        {
          id: 'child1',
          kind: 'member',
          agentId: 2,
          deptId: null,
          memberName: 'bob',
          roleTemplateId: 2,
          model: '',
          driver: '',
          employeeNumber: '',
          deptName: '',
          deptResponsibility: '',
          children: [],
        },
        {
          id: 'child2',
          kind: 'member',
          agentId: 3,
          deptId: null,
          memberName: 'charlie',
          roleTemplateId: 3,
          model: '',
          driver: '',
          employeeNumber: '',
          deptName: '',
          deptResponsibility: '',
          children: [],
        },
      ],
    };

    it('finds root node', () => {
      const found = findNodeById(root, 'root');
      expect(found).not.toBeNull();
      expect(found?.memberName).toBe('alice');
    });

    it('finds child node', () => {
      const found = findNodeById(root, 'child1');
      expect(found).not.toBeNull();
      expect(found?.memberName).toBe('bob');
    });

    it('returns null for non-existent node', () => {
      expect(findNodeById(root, 'nonexistent')).toBeNull();
    });

    it('returns null for null root', () => {
      expect(findNodeById(null, 'any')).toBeNull();
    });
  });

  describe('collectMemberNodes', () => {
    it('collects all member nodes in tree', () => {
      const root: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [
          {
            id: 'child1',
            kind: 'member',
            agentId: 2,
            deptId: null,
            memberName: 'bob',
            roleTemplateId: 2,
            model: '',
            driver: '',
            employeeNumber: '',
            deptName: '',
            deptResponsibility: '',
            children: [],
          },
          {
            id: 'pending1',
            kind: 'pending',
            agentId: null,
            deptId: null,
            memberName: '',
            roleTemplateId: null,
            model: '',
            driver: '',
            employeeNumber: '',
            deptName: '',
            deptResponsibility: '',
            children: [],
          },
        ],
      };

      const members = collectMemberNodes(root);
      expect(members.length).toBe(2);
      expect(members.map((n) => n.memberName)).toContain('alice');
      expect(members.map((n) => n.memberName)).toContain('bob');
    });

    it('returns empty array for null root', () => {
      expect(collectMemberNodes(null)).toEqual([]);
    });
  });

  describe('countManagedChildren', () => {
    it('counts member children only', () => {
      const node: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [
          createPendingNode(),
          createPendingNode(),
        ],
      };
      expect(countManagedChildren(node)).toBe(0);
    });

    it('counts member children correctly', () => {
      const node: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [
          {
            id: 'member1',
            kind: 'member',
            agentId: 2,
            deptId: null,
            memberName: 'bob',
            roleTemplateId: 2,
            model: '',
            driver: '',
            employeeNumber: '',
            deptName: '',
            deptResponsibility: '',
            children: [],
          },
          createPendingNode(),
          {
            id: 'member2',
            kind: 'member',
            agentId: 3,
            deptId: null,
            memberName: 'charlie',
            roleTemplateId: 3,
            model: '',
            driver: '',
            employeeNumber: '',
            deptName: '',
            deptResponsibility: '',
            children: [],
          },
        ],
      };
      expect(countManagedChildren(node)).toBe(2);
    });
  });

  describe('removeNodeById', () => {
    it('removes child node', () => {
      const root: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [
          {
            id: 'child1',
            kind: 'member',
            agentId: 2,
            deptId: null,
            memberName: 'bob',
            roleTemplateId: 2,
            model: '',
            driver: '',
            employeeNumber: '',
            deptName: '',
            deptResponsibility: '',
            children: [],
          },
        ],
      };

      const removed = removeNodeById(root, 'child1');
      expect(removed).toBe(true);
      expect(root.children.length).toBe(0);
    });

    it('removes nested child node', () => {
      const root: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [
          {
            id: 'child1',
            kind: 'member',
            agentId: 2,
            deptId: null,
            memberName: 'bob',
            roleTemplateId: 2,
            model: '',
            driver: '',
            employeeNumber: '',
            deptName: '',
            deptResponsibility: '',
            children: [
              {
                id: 'grandchild',
                kind: 'member',
                agentId: 3,
                deptId: null,
                memberName: 'charlie',
                roleTemplateId: 3,
                model: '',
                driver: '',
                employeeNumber: '',
                deptName: '',
                deptResponsibility: '',
                children: [],
              },
            ],
          },
        ],
      };

      const removed = removeNodeById(root, 'grandchild');
      expect(removed).toBe(true);
      expect(root.children[0].children.length).toBe(0);
    });

    it('returns false for non-existent node', () => {
      const root: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [],
      };

      expect(removeNodeById(root, 'nonexistent')).toBe(false);
    });
  });

  describe('treeHasPendingNode', () => {
    it('returns true when tree has pending nodes', () => {
      const root: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [createPendingNode()],
      };
      expect(treeHasPendingNode(root)).toBe(true);
    });

    it('returns false when no pending nodes', () => {
      const root: DraftOrgNode = {
        id: 'root',
        kind: 'member',
        agentId: 1,
        deptId: null,
        memberName: 'alice',
        roleTemplateId: 1,
        model: '',
        driver: '',
        employeeNumber: '',
        deptName: '',
        deptResponsibility: '',
        children: [],
      };
      expect(treeHasPendingNode(root)).toBe(false);
    });

    it('returns false for null tree', () => {
      expect(treeHasPendingNode(null)).toBe(false);
    });
  });
});