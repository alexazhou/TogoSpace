import { describe, it, expect } from 'vitest';
import type { AgentInfo } from '../../../types';
import {
  cloneDraftOrgNode,
  collectMemberNodes,
  countManagedChildren,
  createDepartmentNameAllocator,
  createDraftNodeId,
  createPendingNode,
  findNodeById,
  isOnBoardAgent,
  parseDriverTypeValue,
  removeNodeById,
  treeHasPendingNode,
  type DraftOrgNode,
} from '../../../utils/teamTreeModel';

// 这些纯函数原本在 TeamTreeEditor.vue 的 <script setup> 里，测试文件**复制了一份同名实现**来测
// ——那样测的是副本，组件改坏了也照样绿（#213 P3 把它抽到 utils/teamTreeModel.ts 后，这里改成
// 直接 import 真实实现，才真正咬住代码）。


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
    const agentWith = (employStatus: string | undefined): AgentInfo => ({ employ_status: employStatus } as AgentInfo);

    it('returns true for ON_BOARD status', () => {
      expect(isOnBoardAgent(agentWith('ON_BOARD'))).toBe(true);
    });

    it('returns true for undefined status', () => {
      expect(isOnBoardAgent(agentWith(undefined))).toBe(true);
    });

    it('returns false for other statuses', () => {
      expect(isOnBoardAgent(agentWith('OFF_BOARD'))).toBe(false);
      expect(isOnBoardAgent(agentWith('PENDING'))).toBe(false);
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