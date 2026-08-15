export type TeamGraphNode = {
  id: string;
  agentId?: number | null;
  kind: 'member' | 'pending';
  name: string;
  departmentName?: string;
  hasDepartment?: boolean;
  subtitle: string;
  employeeNumber?: string;
  avatarName: string;
  avatarSeed?: string;
  children: TeamGraphNode[];
};
