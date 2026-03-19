from __future__ import annotations

import peewee
import peewee_async

_database_proxy: peewee.DatabaseProxy = peewee.DatabaseProxy()


def bind_database(database: peewee.Database) -> None:
    _database_proxy.initialize(database)


class DbModelBase(peewee_async.AioModel):
    class Meta:
        database = _database_proxy
        legacy_table_names = False
