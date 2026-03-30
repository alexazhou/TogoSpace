import os
import sys
from datetime import datetime

import service.ormService as ormService
from dal.db import gtRoleTemplateManager
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from tests.base import ServiceTestCase


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestAutoTimestampMixin(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_tables(self):
        await GtRoleTemplate.delete().aio_execute()
        await GtAgent.delete().aio_execute()
        await GtRoomMessage.delete().aio_execute()
        await GtAgentHistory.delete().aio_execute()
        await GtRoom.delete().aio_execute()
        await GtTeam.delete().aio_execute()

    async def test_db_model_insert_on_conflict_auto_injects_updated_at(self):
        await self._reset_tables()

        old_ts = datetime(2000, 1, 1, 0, 0, 0)
        explicit_ts = datetime(2010, 1, 1, 0, 0, 0)

        await (
            GtRoleTemplate.insert(
                template_name="ts_auto",
                model="glm-4.7",
                created_at=old_ts,
                updated_at=old_ts,
            )
            .aio_execute()
        )

        await (
            GtRoleTemplate.insert(template_name="ts_auto", model="ignored")
            .on_conflict(
                conflict_target=[GtRoleTemplate.template_name],
                update={GtRoleTemplate.model: "gpt-4o"},
            )
            .aio_execute()
        )
        row = await gtRoleTemplateManager.get_role_template_by_name("ts_auto")
        assert row is not None
        assert row.model == "gpt-4o"
        assert row.updated_at > old_ts

        await (
            GtRoleTemplate.insert(template_name="ts_auto", model="ignored")
            .on_conflict(
                conflict_target=[GtRoleTemplate.template_name],
                update={
                    GtRoleTemplate.model: "gpt-4.1",
                    GtRoleTemplate.updated_at: explicit_ts,
                },
            )
            .aio_execute()
        )
        row2 = await gtRoleTemplateManager.get_role_template_by_name("ts_auto")
        assert row2 is not None
        assert row2.model == "gpt-4.1"
        assert row2.updated_at == explicit_ts

    async def test_db_model_insert_auto_injects_created_and_updated_at(self):
        await self._reset_tables()

        before_insert = datetime.now()
        row_id = await (
            GtRoleTemplate.insert(template_name="insert_auto", model="gpt-4o")
            .aio_execute()
        )
        row = await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.id == row_id)
        assert row is not None
        assert row.created_at >= before_insert
        assert row.updated_at >= before_insert

        old_ts = datetime(2001, 1, 1, 0, 0, 0)
        row_id_2 = await (
            GtRoleTemplate.insert(
                template_name="insert_explicit",
                model="glm-4.7",
                created_at=old_ts,
                updated_at=old_ts,
            )
            .aio_execute()
        )
        row2 = await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.id == row_id_2)
        assert row2 is not None
        assert row2.created_at == old_ts
        assert row2.updated_at == old_ts

    async def test_db_model_update_auto_injects_updated_at_for_kwargs_and_dict(self):
        await self._reset_tables()

        old_ts = datetime(2002, 1, 1, 0, 0, 0)
        row_id_1 = await (
            GtRoleTemplate.insert(
                template_name="update_kwargs",
                model="v1",
                created_at=old_ts,
                updated_at=old_ts,
            )
            .aio_execute()
        )
        await (
            GtRoleTemplate.update(model="v2")
            .where(GtRoleTemplate.id == row_id_1)
            .aio_execute()
        )
        row1 = await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.id == row_id_1)
        assert row1 is not None
        assert row1.model == "v2"
        assert row1.updated_at > old_ts

        row_id_2 = await (
            GtRoleTemplate.insert(
                template_name="update_dict",
                model="d1",
                created_at=old_ts,
                updated_at=old_ts,
            )
            .aio_execute()
        )
        await (
            GtRoleTemplate.update({GtRoleTemplate.model: "d2"})
            .where(GtRoleTemplate.id == row_id_2)
            .aio_execute()
        )
        row2 = await GtRoleTemplate.aio_get_or_none(GtRoleTemplate.id == row_id_2)
        assert row2 is not None
        assert row2.model == "d2"
        assert row2.updated_at > old_ts

    async def test_db_model_insert_many_auto_injects_timestamps(self):
        await self._reset_tables()

        before_insert = datetime.now()
        await (
            GtRoleTemplate.insert_many([
                {"template_name": "many_1", "model": "m1"},
                {"template_name": "many_2", "model": "m2"},
            ])
            .aio_execute()
        )
        rows = list(
            await GtRoleTemplate.select()
            .where(GtRoleTemplate.template_name.in_(["many_1", "many_2"]))  # type: ignore[attr-defined]
            .order_by(GtRoleTemplate.template_name)
            .aio_execute()
        )
        assert len(rows) == 2
        assert rows[0].created_at >= before_insert
        assert rows[0].updated_at >= before_insert
        assert rows[1].created_at >= before_insert
        assert rows[1].updated_at >= before_insert

    async def test_db_model_on_conflict_accepts_string_updated_at_key(self):
        await self._reset_tables()

        old_ts = datetime(2003, 1, 1, 0, 0, 0)
        explicit_ts = datetime(2011, 1, 1, 0, 0, 0)

        await (
            GtRoleTemplate.insert(
                template_name="conflict_string_key",
                model="v1",
                created_at=old_ts,
                updated_at=old_ts,
            )
            .aio_execute()
        )
        await (
            GtRoleTemplate.insert(template_name="conflict_string_key", model="ignored")
            .on_conflict(
                conflict_target=[GtRoleTemplate.template_name],
                update={
                    GtRoleTemplate.model: "v2",
                    "updated_at": explicit_ts,
                },
            )
            .aio_execute()
        )
        row = await gtRoleTemplateManager.get_role_template_by_name("conflict_string_key")
        assert row is not None
        assert row.model == "v2"
        assert row.updated_at == explicit_ts

    async def test_db_model_on_conflict_keeps_injection_after_clone_chain(self):
        await self._reset_tables()

        old_ts = datetime(2004, 1, 1, 0, 0, 0)
        await (
            GtRoleTemplate.insert(
                template_name="conflict_clone_chain",
                model="v1",
                created_at=old_ts,
                updated_at=old_ts,
            )
            .aio_execute()
        )
        await (
            GtRoleTemplate.insert(template_name="conflict_clone_chain", model="ignored")
            .returning(GtRoleTemplate.id)
            .on_conflict(
                conflict_target=[GtRoleTemplate.template_name],
                update={GtRoleTemplate.model: "v2"},
            )
            .aio_execute()
        )
        row = await gtRoleTemplateManager.get_role_template_by_name("conflict_clone_chain")
        assert row is not None
        assert row.model == "v2"
        assert row.updated_at > old_ts
