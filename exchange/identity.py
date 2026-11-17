from .audit import write
from .errors import AmbiguousIdentity, IdentityError

# 法律主体之间可以发生的身份变动。更名不改变法律主体本身，单独由名称沿革记录。
RELATION_KINDS = ("merger", "acquisition", "split")


class Identity:
    """可审阅的主体身份关系。

    每个法律主体有稳定的实体编号，名称只是带有效区间的时间段标签。旧名称到期后
    不再能解析到主体，新名称也不能自动代表旧主体。主体之间的合并、收购和分立
    记录为有方向的关系，可以撤回；撤回只影响其后的事实，不改写旧凭据。
    """

    def __init__(self, connection):
        self.connection = connection

    # -- 主体与名称 -------------------------------------------------------

    def register(self, entity_id, name, at, actor=None):
        if not entity_id or not name:
            raise IdentityError("主体编号和名称不能为空")
        if self.connection.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone():
            raise IdentityError("主体编号已存在")
        self._ensure_name_free(name, at, None)
        with self.connection:
            self.connection.execute(
                "INSERT INTO entities(id,display_name,created_at) VALUES(?,?,?)",
                (entity_id, name, at),
            )
            self.connection.execute(
                "INSERT INTO entity_names(entity_id,name,from_at) VALUES(?,?,?)",
                (entity_id, name, at),
            )
            write(self.connection, at, actor or entity_id, "entity.register", entity_id, {"name": name})
        return entity_id

    def add_name(self, entity_id, name, at, actor=None):
        self._require_entity(entity_id)
        self._ensure_name_free(name, at, entity_id)
        with self.connection:
            self.connection.execute(
                "INSERT INTO entity_names(entity_id,name,from_at) VALUES(?,?,?)",
                (entity_id, name, at),
            )
            write(self.connection, at, actor or entity_id, "entity.name.add", entity_id, {"name": name})
        return name

    def rename(self, entity_id, name, at, actor=None):
        self._require_entity(entity_id)
        current = self.names(entity_id, at)
        if not current:
            raise IdentityError("主体在该时点没有有效名称")
        if name in current:
            raise IdentityError("名称没有变化")
        self._ensure_name_free(name, at, entity_id)
        with self.connection:
            self.connection.execute(
                "UPDATE entity_names SET to_at=? WHERE entity_id=? AND from_at<=? "
                "AND (to_at IS NULL OR to_at>?)",
                (at, entity_id, at, at),
            )
            self.connection.execute(
                "INSERT INTO entity_names(entity_id,name,from_at) VALUES(?,?,?)",
                (entity_id, name, at),
            )
            self.connection.execute(
                "UPDATE entities SET display_name=? WHERE id=?", (name, entity_id)
            )
            write(
                self.connection, at, actor or entity_id, "entity.rename", entity_id,
                {"from": current, "to": name},
            )
        return name

    def entity(self, name, at):
        """名称在 at 时点所属的主体；未被任何主体持有则返回 None。"""
        rows = self.connection.execute(
            "SELECT DISTINCT entity_id FROM entity_names WHERE name=? AND from_at<=? "
            "AND (to_at IS NULL OR to_at>?) ORDER BY entity_id",
            (name, at, at),
        ).fetchall()
        if len(rows) > 1:
            raise AmbiguousIdentity("名称在该时点对应多个主体：" + name)
        return rows[0]["entity_id"] if rows else None

    def names(self, entity_id, at):
        return [
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM entity_names WHERE entity_id=? AND from_at<=? "
                "AND (to_at IS NULL OR to_at>?) ORDER BY name",
                (entity_id, at, at),
            )
        ]

    def known(self, name):
        """名称是否曾在台账中登记过（即使当前已失效）。"""
        return (
            self.connection.execute(
                "SELECT 1 FROM entity_names WHERE name=? LIMIT 1", (name,)
            ).fetchone()
            is not None
        )

    def describe(self, entity_id, at):
        row = self.connection.execute(
            "SELECT id,display_name,created_at FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
        if row is None:
            raise KeyError(entity_id)
        return {
            "entity": row["id"],
            "display_name": row["display_name"],
            "names": self.names(entity_id, at),
            "relations": self.relations_for(entity_id, at),
        }

    # -- 主体之间的关系 ---------------------------------------------------

    def relate(self, relation_id, predecessor_id, successor_id, kind, at, actor=None):
        if kind not in RELATION_KINDS:
            raise IdentityError("身份关系类型无效")
        if predecessor_id == successor_id:
            raise IdentityError("主体不能以自己为前身")
        self._require_entity(predecessor_id)
        self._require_entity(successor_id)
        if self.path(successor_id, predecessor_id, at, active_only=False) is not None:
            raise IdentityError("身份关系不能形成循环")
        with self.connection:
            self.connection.execute(
                "INSERT INTO entity_relations(id,predecessor_id,successor_id,kind,at) "
                "VALUES(?,?,?,?,?)",
                (relation_id, predecessor_id, successor_id, kind, at),
            )
            write(
                self.connection, at, actor or predecessor_id, "entity.relate", relation_id,
                {"predecessor": predecessor_id, "successor": successor_id, "kind": kind},
            )
        return relation_id

    def withdraw(self, relation_id, at, actor=None):
        row = self.relation(relation_id)
        if row is None:
            raise KeyError(relation_id)
        if row["status"] != "active":
            raise IdentityError("身份关系已经撤回")
        if at < row["at"]:
            raise IdentityError("撤回时间早于关系生效时间")
        with self.connection:
            self.connection.execute(
                "UPDATE entity_relations SET status='withdrawn',withdrawn_at=?,withdrawn_by=? "
                "WHERE id=?",
                (at, actor or row["predecessor_id"], relation_id),
            )
            write(
                self.connection, at, actor or row["predecessor_id"], "entity.withdraw", relation_id,
                {"predecessor": row["predecessor_id"], "successor": row["successor_id"], "kind": row["kind"]},
            )
        return relation_id

    def relation(self, relation_id):
        row = self.connection.execute(
            "SELECT * FROM entity_relations WHERE id=?", (relation_id,)
        ).fetchone()
        return dict(row) if row else None

    def relations_for(self, entity_id, at):
        rows = self.connection.execute(
            "SELECT * FROM entity_relations WHERE (predecessor_id=? OR successor_id=?) AND at<=? "
            "ORDER BY id",
            (entity_id, entity_id, at),
        ).fetchall()
        return [dict(row) for row in rows]

    def path(self, predecessor_id, successor_id, at, active_only=True):
        """predecessor 到 successor 的最短身份关系链；不存在则返回 None。

        active_only=False 时把已撤回的关系也算进来，用于审计区分“从未有关系”
        与“关系已被撤回”。
        """
        if predecessor_id == successor_id:
            return []
        queue = [(predecessor_id, [])]
        seen = {predecessor_id}
        while queue:
            node, trail = queue.pop(0)
            if len(trail) >= 8:
                continue
            for row in self.connection.execute(
                "SELECT * FROM entity_relations WHERE predecessor_id=? AND at<=? ORDER BY id",
                (node, at),
            ):
                relation = dict(row)
                if active_only and not (
                    relation["status"] == "active"
                    and (relation["withdrawn_at"] is None or relation["withdrawn_at"] > at)
                ):
                    continue
                step = trail + [relation]
                if relation["successor_id"] == successor_id:
                    return step
                if relation["successor_id"] not in seen:
                    seen.add(relation["successor_id"])
                    queue.append((relation["successor_id"], step))
        return None

    # -- 内部工具 ---------------------------------------------------------

    def _require_entity(self, entity_id):
        if self.connection.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone() is None:
            raise KeyError(entity_id)

    def _ensure_name_free(self, name, at, entity_id):
        # 新登记的名称从 at 起一直有效，因此只与在该时点之后仍未结束的同名
        # 区间冲突；已经结束的旧名称可以依法由其他主体重新使用，按时间区分。
        row = self.connection.execute(
            "SELECT entity_id FROM entity_names WHERE name=? AND (to_at IS NULL OR to_at>?) "
            "AND (entity_id<>? OR ? IS NULL) LIMIT 1",
            (name, at, entity_id if entity_id is not None else "", entity_id),
        ).fetchone()
        if row is not None:
            raise IdentityError("名称在该时点之后仍属于其他主体：" + name)
