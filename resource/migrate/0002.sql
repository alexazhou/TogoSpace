-- Rename 'agents' table to 'role_templates' (was storing role template / AgentTemplate data)
ALTER TABLE agents RENAME TO role_templates;

-- Rename 'team_members' table to 'agents' (was storing runtime TeamMember data)
ALTER TABLE team_members RENAME TO agents;

-- Rename column: agents.agent_name -> agents.role_template_name
ALTER TABLE agents RENAME COLUMN agent_name TO role_template_name;

-- Rename 'member_histories' table to 'agent_histories'
ALTER TABLE member_histories RENAME TO agent_histories;

-- Rename column: agent_histories.member_id -> agent_histories.agent_id
ALTER TABLE agent_histories RENAME COLUMN member_id TO agent_id;

-- Rename column: room_messages.member_id -> room_messages.agent_id
ALTER TABLE room_messages RENAME COLUMN member_id TO agent_id;

-- Rename column: rooms.member_ids -> rooms.agent_ids
ALTER TABLE rooms RENAME COLUMN member_ids TO agent_ids;

-- Rename column: rooms.member_read_index -> rooms.agent_read_index
ALTER TABLE rooms RENAME COLUMN member_read_index TO agent_read_index;

-- Rename column: depts.member_ids -> depts.agent_ids
ALTER TABLE depts RENAME COLUMN member_ids TO agent_ids;
